package com.bank.qa.tests.data;

import com.bank.qa.api.SchemaValidator;
import com.bank.qa.auth.TotpGenerator;
import com.bank.qa.data.BoundaryValues;
import com.bank.qa.data.FinancialIdentifiers;
import com.bank.qa.data.PiiMasker;
import com.bank.qa.data.SyntheticDataFactory;
import com.bank.qa.data.SyntheticDataFactory.CardProfile;
import com.bank.qa.mock.MockSchemaGenerator;
import com.fasterxml.jackson.databind.JsonNode;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.EnumSource;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * Fast, browser-free tests of the framework's own data tooling. Run first in CI ("unit" stage):
 * if the data generator is broken, every E2E test would fail for the wrong reason.
 */
@Tag("unit")
class SyntheticDataAndMockGenerationTest {

    private final SyntheticDataFactory data = SyntheticDataFactory.forTest("unit-test");

    @ParameterizedTest
    @EnumSource(CardProfile.class)
    void generated_card_numbers_pass_luhn_and_use_reserved_test_bins(CardProfile profile) {
        for (int i = 0; i < 50; i++) {
            String pan = data.cardNumber(profile);
            assertThat(pan).hasSize(16).startsWith(profile.bin());
            assertThat(FinancialIdentifiers.luhnValid(pan)).as(pan).isTrue();
        }
    }

    @Test
    void broken_check_digit_is_detected() {
        assertThat(FinancialIdentifiers.luhnValid(data.invalidLuhnCardNumber())).isFalse();
        assertThat(FinancialIdentifiers.luhnValid("4111111111111111")).isTrue(); // well-known test PAN
    }

    @Test
    void generated_ibans_have_valid_mod97_check_digits() {
        for (int i = 0; i < 50; i++) {
            String iban = data.iban();
            assertThat(iban).startsWith("GB").hasSize(22).contains("TEST");
            assertThat(FinancialIdentifiers.ibanValid(iban)).as(iban).isTrue();
        }
        assertThat(FinancialIdentifiers.ibanValid("GB82WEST12345698765432")).isTrue();  // ECBS example IBAN
        assertThat(FinancialIdentifiers.ibanValid("GB83WEST12345698765432")).isFalse();
    }

    @Test
    void same_seed_and_test_id_reproduce_identical_data() {
        SyntheticDataFactory a = SyntheticDataFactory.forTest("repro");
        SyntheticDataFactory b = SyntheticDataFactory.forTest("repro");
        assertThat(a.cardNumber(CardProfile.APPROVE)).isEqualTo(b.cardNumber(CardProfile.APPROVE));
        assertThat(a.iban()).isEqualTo(b.iban());
        assertThat(a.companyName()).isEqualTo(b.companyName());
    }

    @Test
    void references_are_unique_and_fit_the_18_char_limit() {
        java.util.Set<String> refs = new java.util.HashSet<>();
        for (int i = 0; i < 500; i++) {
            String ref = data.uniqueReference();
            assertThat(ref).matches("^[A-Za-z0-9 \\-]{1,18}$");
            refs.add(ref);
        }
        assertThat(refs).hasSize(500);
    }

    @Test
    void pii_is_masked_in_free_text_and_json() {
        String pan = data.cardNumber(CardProfile.APPROVE);
        String masked = PiiMasker.mask("{\"pan\":\"" + pan + "\",\"password\":\"Secret1\",\"note\":\"card " + pan + " iban GB82WEST12345698765432\"}");
        assertThat(masked).doesNotContain(pan).doesNotContain("Secret1").doesNotContain("12345698765432")
                .contains(pan.substring(0, 6) + "******" + pan.substring(12));
        assertThat(PiiMasker.maskPan("4000001234567899")).isEqualTo("400000******7899");
    }

    @Test
    void boundary_value_generator_derives_cases_from_acceptance_criteria() {
        List<BoundaryValues.Case<BigDecimal>> cases =
                BoundaryValues.range(new BigDecimal("0.01"), new BigDecimal("50000.00"), new BigDecimal("0.01"));
        assertThat(cases).extracting(c -> c.value().toPlainString())
                .containsExactly("0.00", "0.01", "0.02", "25000.00", "49999.99", "50000.00", "50000.01");
        assertThat(cases).extracting(BoundaryValues.Case::valid)
                .containsExactly(false, true, true, true, true, true, false);
        assertThat(BoundaryValues.length(1, 18, 'R')).extracting(c -> c.value().length())
                .containsExactly(0, 1, 2, 17, 18, 19);
    }

    @Test
    void totp_matches_rfc6238_reference_vector() {
        // RFC 6238 Appendix B, SHA1, secret "12345678901234567890", T=59s -> 94287082 (8 digits) => 287082 (6 digits)
        assertThat(TotpGenerator.at("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ", Instant.ofEpochSecond(59))).isEqualTo("287082");
    }

    // ------------------------------------------------------------------ mock schema generation

    @Test
    void mock_generated_from_schema_is_contract_valid_and_domain_realistic() {
        MockSchemaGenerator gen = new MockSchemaGenerator(data);
        JsonNode payment = gen.generateValid("payment.schema.json", Map.of("/status", "SETTLED", "/amount", "12500.00"));
        assertThat(payment.get("paymentId").asText()).matches("PAY-[A-Z0-9]{8}");
        assertThat(payment.get("status").asText()).isEqualTo("SETTLED");
        assertThat(payment.get("amount").asText()).isEqualTo("12500.00");
        assertThat(payment.get("reference").asText()).startsWith("QA-");
        assertThat(SchemaValidator.validate("payment.schema.json", payment)).isEmpty();

        JsonNode fx = gen.generateValid("fx-rates.schema.json", Map.of("/rates/EUR", 1.2345));
        assertThat(fx.at("/rates/EUR").asDouble()).isEqualTo(1.2345);
        assertThat(fx.get("base").asText()).isEqualTo("GBP");
    }

    @Test
    void mock_that_drifts_from_the_contract_is_rejected() {
        MockSchemaGenerator gen = new MockSchemaGenerator(data);
        Map<String, Object> drift = new HashMap<>();
        drift.put("/status", "PAID");      // not in enum
        drift.put("/currency", null);      // required field removed
        assertThatThrownBy(() -> gen.generateValid("payment.schema.json", drift))
                .isInstanceOf(AssertionError.class)
                .hasMessageContaining("currency")
                .hasMessageContaining("status");
    }
}
