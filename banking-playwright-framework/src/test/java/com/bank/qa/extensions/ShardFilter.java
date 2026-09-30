package com.bank.qa.extensions;

import org.junit.platform.engine.FilterResult;
import org.junit.platform.engine.TestDescriptor;
import org.junit.platform.engine.support.descriptor.ClassSource;
import org.junit.platform.engine.support.descriptor.MethodSource;
import org.junit.platform.launcher.PostDiscoveryFilter;

/**
 * CI SHARDING for JUnit 5: {@code -Dshard=2/4} runs only the test classes whose stable hash falls into
 * shard 2 of 4. Four CI jobs with shard=1/4..4/4 together run the whole suite exactly once.
 *
 * <ul>
 *   <li>Sharding is by CLASS so class-level setup and {@code @Nested} tests stay together.</li>
 *   <li>Hash of the class name is deterministic: the same class always lands on the same shard, which
 *       makes "which job ran my test?" easy and cache-friendly.</li>
 *   <li>Registered through {@code META-INF/services/org.junit.platform.launcher.PostDiscoveryFilter}
 *       - no change to test code needed.</li>
 * </ul>
 * (The Node Playwright runner has {@code --shard=2/4} built in; Java needs this small filter.)
 */
public final class ShardFilter implements PostDiscoveryFilter {

    @Override
    public FilterResult apply(TestDescriptor d) {
        String spec = System.getProperty("shard", "");
        if (spec.isBlank() || !spec.matches("\\d+/\\d+")) return FilterResult.included("sharding disabled");
        int index = Integer.parseInt(spec.split("/")[0]);
        int total = Integer.parseInt(spec.split("/")[1]);
        String className = className(d);
        if (className == null) return FilterResult.included("not a class-bound descriptor");
        String topLevel = className.contains("$") ? className.substring(0, className.indexOf('$')) : className;
        int bucket = Math.floorMod(topLevel.hashCode(), total) + 1;
        return bucket == index
                ? FilterResult.included("shard " + spec)
                : FilterResult.excluded("belongs to shard " + bucket + "/" + total);
    }

    private static String className(TestDescriptor d) {
        for (TestDescriptor cur = d; cur != null; cur = cur.getParent().orElse(null)) {
            if (cur.getSource().orElse(null) instanceof ClassSource cs) return cs.getClassName();
            if (cur.getSource().orElse(null) instanceof MethodSource ms) return ms.getClassName();
        }
        return null;
    }
}
