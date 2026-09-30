package com.bank.qa.sampleapp;

import com.bank.qa.auth.TotpGenerator;

/** Prints the current MFA codes of the sample app's demo users (for manual exploration only). */
public final class PrintTotp {
    public static void main(String[] args) {
        new BankStore("n/a").users.values().forEach(u ->
                System.out.printf("%-14s %-8s %s%n", u.username(), u.role(), TotpGenerator.now(u.totpSecret())));
    }
}
