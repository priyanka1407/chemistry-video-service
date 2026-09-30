package com.bank.qa.tests.authorization;

import com.bank.qa.api.BankApiClient;
import com.bank.qa.api.SchemaValidator;
import com.bank.qa.auth.Role;
import com.bank.qa.data.FinancialIdentifiers;
import com.bank.qa.data.PiiMasker;
import com.bank.qa.data.SyntheticDataFactory;
import com.bank.qa.data.SyntheticDataFactory.CardProfile;
import com.bank.qa.extensions.AsRole;
import com.bank.qa.extensions.PlaywrightTest;
import com.bank.qa.mock.NetworkMocks;
import com.bank.qa.pages.CardAuthorizationPage;
import com.bank.qa.pages.CardAuthorizationPage.CardInput;
import com.microsoft.playwright.Page;
import com.microsoft.playwright.Request;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;

import java.time.YearMonth;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Pattern;

import static com.microsoft.playwright.assertions.PlaywrightAssertions.assertThat;
import static org.assertj.core.api.Assertions.assertThat;

/**
 * User story CARD-201 - card authorization (simplified ISO 8583 response codes):
 * <pre>
 *  00 Approved | 05 Do not honour | 14 Invalid card number | 51 Insufficient funds | 54 Expired card
 *  N7 CVV2 failure | 1A Strong Customer Authentication (3-D Secure) required | N0 authentication failed
 *  AC1 amount 0.01..10,000.00; available limit 5,000.00
 *  AC2 amounts above 250.00 require a 3-D Secure challenge (PSD2 SCA)
 *  AC3 a card is valid until the LAST day of its expiry month
 *  AC4 responses/UI show at most first 6 + last 4 PAN digits (PCI-DSS 3.4)
 * </pre>
 * Outcomes are driven by synthetic cards from reserved TEST BINs - exactly how issuer simulators work.
 */
@PlaywrightTest
@AsRole(Role.MAKER)
@Tag("authorization")
class CardAuthorizationTest {

    private static String expiryPlusMonths(int months) {
        return YearMonth.now(ZoneId.of("Europe/London")).plusMonths(months).format(DateTimeFormatter.ofPattern("MM/yy"));
    }

    @ParameterizedTest(name = "{0} {1} -> {2} {3}")
    @CsvSource({
            "APPROVE,            100.00, APPROVED, 00",
            "DO_NOT_HONOUR,      100.00, DECLINED, 05",
            "INSUFFICIENT_FUNDS, 100.00, DECLINED, 51"})
    @Tag("smoke")
    void issuer_outcome_is_driven_by_test_bin(CardProfile profile, String amount, String decision, String code,
                                              Page page, SyntheticDataFactory data) {
        String pan = data.cardNumber(profile);
        CardAuthorizationPage cards = new CardAuthorizationPage(page).open();
        cards.authorize(new CardInput(pan, data.futureExpiry(), data.cvv(), amount, "5411 - Grocery stores"));

        assertThat(cards.decision()).hasText(decision);
        assertThat(cards.responseCode()).hasText(code);
        // PCI: the UI shows the masked PAN only - never the full number.
        assertThat(cards.maskedPan()).hasText(PiiMasker.maskPan(pan));
        assertThat(page.getByText(pan)).hasCount(0);
    }

    @Test
    void invalid_luhn_and_expired_card_are_declined(BankApiClient api, SyntheticDataFactory data) {
        BankApiClient.Response badLuhn = api.authorizeCard(card(data.invalidLuhnCardNumber(), data.futureExpiry(), "10.00"));
        assertThat(badLuhn.text("responseCode")).isEqualTo("14");

        BankApiClient.Response expired = api.authorizeCard(card(data.cardNumber(CardProfile.APPROVE), expiryPlusMonths(-1), "10.00"));
        assertThat(expired.text("responseCode")).isEqualTo("54");
    }

    @ParameterizedTest(name = "AC3 expiry {0} months from now -> {1}")
    @CsvSource({"-1, 54", "0, 00", "1, 00"})
    void expiry_month_boundaries(int monthOffset, String expectedCode, BankApiClient api, SyntheticDataFactory data) {
        BankApiClient.Response res = api.authorizeCard(card(data.cardNumber(CardProfile.APPROVE), expiryPlusMonths(monthOffset), "10.00"));
        assertThat(res.text("responseCode")).isEqualTo(expectedCode);
    }

    @ParameterizedTest(name = "AC1/AC2 amount {0} -> HTTP {1} {2}")
    @CsvSource({
            "0.00,     422, ",
            "0.01,     200, APPROVED",
            "250.00,   200, APPROVED",
            "250.01,   200, CHALLENGE",
            "5000.00,  200, CHALLENGE",
            "5000.01,  200, DECLINED",
            "10000.00, 200, DECLINED",
            "10000.01, 422, "})
    void amount_and_sca_threshold_boundaries(String amount, int httpStatus, String decision, BankApiClient api, SyntheticDataFactory data) {
        BankApiClient.Response res = api.authorizeCard(card(data.cardNumber(CardProfile.APPROVE), data.futureExpiry(), amount));
        assertThat(res.status()).isEqualTo(httpStatus);
        if (httpStatus == 200) {
            SchemaValidator.assertValid("card-authorization.schema.json", res.body()); // also asserts no raw PAN/CVV echoed
            assertThat(res.text("decision")).isEqualTo(decision);
        }
    }

    @Test
    @Tag("smoke")
    void three_d_secure_challenge_inside_iframe_approves_with_correct_otp(Page page, SyntheticDataFactory data) {
        CardAuthorizationPage cards = new CardAuthorizationPage(page).open();
        cards.authorize(new CardInput(data.cardNumber(CardProfile.APPROVE), data.futureExpiry(), data.cvv(), "300.00", null));

        assertThat(cards.decision()).hasText("CHALLENGE");
        assertThat(cards.challengeIframe()).isVisible();
        // frameLocator auto-waits for the iframe document to load before looking inside it.
        assertThat(cards.challengeFrame().getByRole(com.microsoft.playwright.options.AriaRole.HEADING)).hasText("Verify your purchase");

        cards.completeChallenge("123456");

        // Result arrives via window.postMessage from the iframe to the parent page.
        assertThat(cards.decision()).hasText("APPROVED");
        assertThat(cards.responseMessage()).hasText("Approved after 3-D Secure");
        assertThat(cards.challengeIframe()).hasCount(0);
    }

    @Test
    void three_d_secure_wrong_otp_declines(Page page, SyntheticDataFactory data) {
        CardAuthorizationPage cards = new CardAuthorizationPage(page).open();
        cards.authorize(new CardInput(data.cardNumber(CardProfile.APPROVE), data.futureExpiry(), data.cvv(), "999.99", null));
        cards.completeChallenge("000000");
        assertThat(cards.decision()).hasText("DECLINED");
        assertThat(cards.responseCode()).hasText("N0");
    }

    @Test
    void card_data_is_sent_only_to_first_party_and_cvv_never_leaves_in_the_url(Page page, SyntheticDataFactory data) {
        List<Request> traffic = NetworkMocks.recordRequests(page, Pattern.compile(".*"));
        String pan = data.cardNumber(CardProfile.APPROVE);
        String cvv = data.cvv();
        CardAuthorizationPage cards = new CardAuthorizationPage(page).open();

        Request auth = page.waitForRequest("**/api/cards/authorize",
                () -> cards.authorize(new CardInput(pan, data.futureExpiry(), cvv, "20.00", null)));
        assertThat(auth.method()).isEqualTo("POST");
        assertThat(auth.postData()).contains(pan); // PAN only in the POST body (TLS in real envs)
        assertThat(cards.decision()).hasText("APPROVED");

        String origin = page.url().replaceAll("^(https?://[^/]+).*$", "$1");
        assertThat(traffic).allSatisfy(r -> {
            assertThat(r.url()).startsWith(origin);                      // no third-party receives card data
            assertThat(r.url()).doesNotContain(pan).doesNotContain("cvv");
        });
        assertThat(FinancialIdentifiers.luhnValid(pan)).isTrue();
    }

    private static Map<String, Object> card(String pan, String expiry, String amount) {
        Map<String, Object> m = new HashMap<>();
        m.put("pan", pan);
        m.put("expiry", expiry);
        m.put("cvv", "123");
        m.put("amount", amount);
        m.put("currency", "GBP");
        m.put("mcc", "5411");
        m.put("merchant", "QA Test Merchant");
        return m;
    }
}
