package com.bank.qa.tests.payments;

import com.bank.qa.api.BankApiClient;
import com.bank.qa.auth.Role;
import com.bank.qa.data.BoundaryValues;
import com.bank.qa.data.BoundaryValues.Case;
import com.bank.qa.data.SyntheticDataFactory;
import com.bank.qa.extensions.AsRole;
import com.bank.qa.extensions.PlaywrightTest;
import com.bank.qa.pages.PaymentPage;
import com.bank.qa.pages.PaymentPage.PaymentForm;
import com.bank.qa.support.PaymentPayloads;
import com.microsoft.playwright.Page;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import org.junit.jupiter.params.provider.MethodSource;
import org.junit.jupiter.params.provider.ValueSource;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.ZoneId;
import java.util.stream.Stream;

import static com.microsoft.playwright.assertions.PlaywrightAssertions.assertThat;
import static org.assertj.core.api.Assertions.assertThat;

/**
 * BOUNDARY VALUE ANALYSIS derived from user story PAY-101 acceptance criteria:
 * <pre>
 *  AC1  amount: 0.01 <= amount <= 50,000.00, max 2 decimal places
 *  AC2  amount > 10,000.00 requires checker approval (internal threshold)
 *  AC3  reference: required, 1..18 chars, letters/digits/space/hyphen
 *  AC4  payment date: today .. today + 30 days (Europe/London)
 * </pre>
 * Strategy (test pyramid): the full BVA/EP matrix runs at the API layer (milliseconds per case);
 * the UI layer re-checks only the outermost boundaries to prove messages are wired to the fields.
 */
@PlaywrightTest
@AsRole(Role.MAKER)
@Tag("payments")
@Tag("bva")
class PaymentBoundaryValueTest {

    private static final BigDecimal CENT = new BigDecimal("0.01");

    static Stream<Case<BigDecimal>> amountBoundaries() {
        return BoundaryValues.range(new BigDecimal("0.01"), new BigDecimal("50000.00"), CENT).stream();
    }

    @ParameterizedTest(name = "AC1 amount {0}")
    @MethodSource("amountBoundaries")
    void amount_range_boundaries(Case<BigDecimal> c, BankApiClient api, SyntheticDataFactory data) {
        BankApiClient.Response res = api.createPayment(PaymentPayloads.valid(data, c.value().toPlainString()));
        if (c.valid()) {
            assertThat(res.status()).as(c.toString()).isEqualTo(201);
        } else {
            assertThat(res.status()).as(c.toString()).isEqualTo(422);
            assertThat(res.body().at("/error/fields/0/field").asText()).isEqualTo("amount");
        }
    }

    @ParameterizedTest(name = "AC1 amount format ''{0}'' is rejected")
    @ValueSource(strings = {"abc", "10.001", "-5.00", "1e3", "1,000.00", " ", "0"})
    void amount_invalid_equivalence_classes(String amount, BankApiClient api, SyntheticDataFactory data) {
        BankApiClient.Response res = api.createPayment(PaymentPayloads.valid(data, amount));
        assertThat(res.status()).isEqualTo(422);
        assertThat(res.errorCode()).isEqualTo("VALIDATION_FAILED");
    }

    static Stream<Case<BigDecimal>> approvalThreshold() {
        return BoundaryValues.threshold(new BigDecimal("10000.00"), CENT).stream();
    }

    @ParameterizedTest(name = "AC2 threshold {0}")
    @MethodSource("approvalThreshold")
    void approval_threshold_boundaries(Case<BigDecimal> c, BankApiClient api, SyntheticDataFactory data) {
        BankApiClient.Response res = api.createPayment(PaymentPayloads.valid(data, c.value().toPlainString()));
        assertThat(res.status()).isEqualTo(201);
        // "valid" side = at/below threshold -> released straight to the settlement queue.
        assertThat(res.text("status")).as(c.toString()).isEqualTo(c.valid() ? "PENDING_SETTLEMENT" : "PENDING_APPROVAL");
    }

    static Stream<Case<String>> referenceLengths() {
        return BoundaryValues.length(1, 18, 'R').stream();
    }

    @ParameterizedTest(name = "AC3 reference length {0}")
    @MethodSource("referenceLengths")
    void reference_length_boundaries(Case<String> c, BankApiClient api, SyntheticDataFactory data) {
        BankApiClient.Response res = api.createPayment(PaymentPayloads.with(PaymentPayloads.valid(data, "10.00"), "reference", c.value()));
        assertThat(res.status()).as(c.toString()).isEqualTo(c.valid() ? 201 : 422);
    }

    @ParameterizedTest(name = "AC4 execution date today{0} -> {1}")
    @CsvSource({"-1, 422", "0, 201", "1, 201", "29, 201", "30, 201", "31, 422"})
    void execution_date_boundaries(int offsetDays, int expectedStatus, BankApiClient api, SyntheticDataFactory data) {
        String date = LocalDate.now(ZoneId.of("Europe/London")).plusDays(offsetDays).toString();
        BankApiClient.Response res = api.createPayment(PaymentPayloads.with(PaymentPayloads.valid(data, "10.00"), "executionDate", date));
        assertThat(res.status()).isEqualTo(expectedStatus);
    }

    // ---------------------------------------------------------------- UI: outermost boundaries only

    @ParameterizedTest(name = "UI amount {0} -> ''{1}''")
    @CsvSource(delimiter = '|', value = {
            "0.00     | Amount must be at least 0.01",
            "50000.01 | Amount must not exceed 50,000.00",
            "12.345   | Amount must be a number with at most 2 decimal places"})
    void ui_shows_field_level_error_for_out_of_range_amounts(String amount, String message, Page page, SyntheticDataFactory data) {
        PaymentPage payments = new PaymentPage(page).open();
        payments.fill(new PaymentForm("Operating Account", "north", "Northwind Traders", "GBP", amount, data.uniqueReference(), null))
                .review().confirm();
        assertThat(payments.fieldError("amount")).hasText(message);
        assertThat(payments.amount()).hasAttribute("aria-invalid", "true"); // accessible error state
        assertThat(payments.paymentId()).isHidden();
    }

    @ParameterizedTest(name = "UI reference of {0} chars")
    @ValueSource(ints = {18, 19})
    void ui_reference_max_length(int length, Page page) {
        PaymentPage payments = new PaymentPage(page).open();
        String ref = "R".repeat(length);
        payments.fill(new PaymentForm("Operating Account", "north", "Northwind Traders", "GBP", "10.00", ref, null)).review().confirm();
        if (length <= 18) {
            assertThat(payments.paymentId()).isVisible();
        } else {
            assertThat(payments.fieldError("reference")).hasText("Reference must be 18 characters or fewer");
        }
    }

}
