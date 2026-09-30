package com.bank.qa.support;

import com.bank.qa.data.SyntheticDataFactory;

import java.util.HashMap;
import java.util.Map;

/**
 * Builds API payloads for payments. Test-data builder pattern: a valid default for every field,
 * tests override only what they are about - which keeps each test's intent obvious.
 */
public final class PaymentPayloads {

    public static final String OPERATING_ACCOUNT = "ACC-001";
    public static final String NORTHWIND = "BEN-103";

    private PaymentPayloads() {}

    public static Map<String, Object> valid(SyntheticDataFactory data, String amount) {
        Map<String, Object> p = new HashMap<>();
        p.put("debtorAccountId", OPERATING_ACCOUNT);
        p.put("beneficiaryId", NORTHWIND);
        p.put("currency", "GBP");
        p.put("amount", amount);
        p.put("reference", data.uniqueReference());
        p.put("executionDate", data.today());
        return p;
    }

    public static Map<String, Object> with(Map<String, Object> base, String field, Object value) {
        Map<String, Object> copy = new HashMap<>(base);
        copy.put(field, value);
        return copy;
    }
}
