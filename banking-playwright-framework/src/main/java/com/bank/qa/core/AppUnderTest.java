package com.bank.qa.core;

import com.bank.qa.config.FrameworkConfig;
import com.bank.qa.sampleapp.SampleBankServer;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Resolves the base URL of the application under test.
 *
 * <p>When {@code app.embedded=true} the bundled sample bank is started ONCE per JVM on a random free
 * port (port 0), which makes parallel CI jobs on the same host collision-free. Otherwise the
 * configured {@code base.url} (e.g. UAT) is used.
 */
public final class AppUnderTest {

    private static final Logger log = LoggerFactory.getLogger(AppUnderTest.class);
    private static volatile String baseUrl;

    private AppUnderTest() {}

    public static String baseUrl() {
        if (baseUrl == null) {
            synchronized (AppUnderTest.class) {
                if (baseUrl == null) baseUrl = resolve();
            }
        }
        return baseUrl;
    }

    private static String resolve() {
        FrameworkConfig cfg = FrameworkConfig.get();
        if (!cfg.embeddedApp()) {
            String url = cfg.get("base.url");
            if (url == null) throw new IllegalStateException("base.url must be set when app.embedded=false");
            return url.replaceAll("/$", "");
        }
        try {
            SampleBankServer server = new SampleBankServer(Integer.parseInt(cfg.get("app.port", "0")), "Test@12345", true);
            server.start();
            Runtime.getRuntime().addShutdownHook(new Thread(server::stop, "sample-bank-shutdown"));
            log.info("Embedded sample bank started at {}", server.baseUrl());
            return server.baseUrl();
        } catch (Exception e) {
            throw new IllegalStateException("Could not start embedded sample bank", e);
        }
    }
}
