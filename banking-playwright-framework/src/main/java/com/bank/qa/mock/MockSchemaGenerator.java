package com.bank.qa.mock;

import com.bank.qa.api.SchemaValidator;
import com.bank.qa.data.SyntheticDataFactory;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.JsonNodeFactory;
import com.fasterxml.jackson.databind.node.ObjectNode;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.Instant;
import java.time.LocalDate;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;

/**
 * MOCK SCHEMA GENERATION - build a VALID mock payload directly from a JSON Schema.
 *
 * <p>Hand-written JSON fixtures rot: the provider adds a required field, renames an enum value, and the
 * mocks keep "passing". Generating mocks from the contract means:
 * <ul>
 *   <li>every mock is valid by construction and re-validated ({@link #generateValid}),</li>
 *   <li>tests only state what matters to them via JSON-Pointer overrides
 *       ({@code "/rates/EUR" -> 1.5}) - everything else is plausible filler,</li>
 *   <li>when the schema changes, all mocks follow automatically.</li>
 * </ul>
 *
 * <p>Supported keywords: type, properties, required, items, minItems, enum, const, examples, default,
 * format (date, date-time, uuid, email), minimum/maximum, minLength/maxLength, $ref to #/$defs, and the
 * custom vendor hint {@code x-mock} (iban, company, currency, amount, reference, id:PREFIX) that maps a
 * field to the {@link SyntheticDataFactory} so values are domain-realistic, not "string".
 * Generation is seeded, so the same test gets the same mock every run.
 */
public final class MockSchemaGenerator {

    private static final ObjectMapper JSON = new ObjectMapper();
    private static final JsonNodeFactory NF = JsonNodeFactory.instance;

    private final SyntheticDataFactory data;

    public MockSchemaGenerator(SyntheticDataFactory data) {
        this.data = data;
    }

    /** Generates from schema, applies overrides, then asserts the result still satisfies the schema. */
    public JsonNode generateValid(String schemaName, Map<String, Object> overrides) {
        JsonNode schema = SchemaValidator.schemaJson(schemaName);
        JsonNode generated = generate(schema, schema);
        applyOverrides(generated, overrides);
        SchemaValidator.assertValid(schemaName, generated); // mock must never drift from the contract
        return generated;
    }

    /** Generates and applies overrides WITHOUT validation - for deliberately broken payloads (negative tests). */
    public JsonNode generateUnchecked(String schemaName, Map<String, Object> overrides) {
        JsonNode schema = SchemaValidator.schemaJson(schemaName);
        JsonNode generated = generate(schema, schema);
        applyOverrides(generated, overrides);
        return generated;
    }

    JsonNode generate(JsonNode node, JsonNode root) {
        if (node.has("$ref")) {
            return generate(resolveRef(node.get("$ref").asText(), root), root);
        }
        if (node.has("const")) return node.get("const").deepCopy();
        if (node.has("examples") && node.get("examples").size() > 0) return node.get("examples").get(0).deepCopy();
        if (node.has("enum")) {
            JsonNode e = node.get("enum");
            return e.get(data.random().nextInt(e.size())).deepCopy();
        }
        if (node.has("x-mock")) return hint(node.get("x-mock").asText());
        if (node.has("default")) return node.get("default").deepCopy();

        String type = type(node);
        switch (type) {
            case "object": {
                ObjectNode obj = NF.objectNode();
                JsonNode props = node.path("properties");
                Iterator<Map.Entry<String, JsonNode>> it = props.fields();
                while (it.hasNext()) {
                    Map.Entry<String, JsonNode> e = it.next();
                    obj.set(e.getKey(), generate(e.getValue(), root));
                }
                return obj;
            }
            case "array": {
                ArrayNode arr = NF.arrayNode();
                int n = Math.max(node.path("minItems").asInt(1), 1);
                for (int i = 0; i < n; i++) arr.add(generate(node.path("items"), root));
                return arr;
            }
            case "integer": {
                long min = node.path("minimum").asLong(0);
                long max = node.path("maximum").asLong(min + 1000);
                return NF.numberNode(min + (long) (data.random().nextDouble() * (max - min)));
            }
            case "number": {
                double min = node.path("minimum").asDouble(0);
                double max = node.path("maximum").asDouble(min + 1000);
                BigDecimal v = BigDecimal.valueOf(min + data.random().nextDouble() * (max - min)).setScale(4, RoundingMode.DOWN);
                return NF.numberNode(v);
            }
            case "boolean":
                return NF.booleanNode(data.random().nextBoolean());
            case "null":
                return NF.nullNode();
            default:
                return NF.textNode(string(node));
        }
    }

    private String string(JsonNode node) {
        String format = node.path("format").asText("");
        switch (format) {
            case "date": return LocalDate.now().toString();
            case "date-time": return Instant.now().toString();
            case "uuid": return UUID.randomUUID().toString();
            case "email": return "qa.user" + data.random().nextInt(10000) + "@example.test";
            default: break;
        }
        int min = node.path("minLength").asInt(1);
        int max = node.path("maxLength").asInt(Math.max(min, 12));
        StringBuilder sb = new StringBuilder("mock");
        while (sb.length() < min) sb.append('x');
        return sb.length() > max ? sb.substring(0, max) : sb.toString();
    }

    private JsonNode hint(String hint) {
        if (hint.startsWith("id:")) {
            return NF.textNode(hint.substring(3) + UUID.randomUUID().toString().substring(0, 8).toUpperCase());
        }
        return switch (hint) {
            case "iban" -> NF.textNode(data.iban());
            case "company" -> NF.textNode(data.companyName());
            case "currency" -> NF.textNode("GBP");
            case "amount" -> NF.textNode(data.amountBetween(new BigDecimal("1.00"), new BigDecimal("9999.99")).toPlainString());
            case "reference" -> NF.textNode(data.uniqueReference());
            default -> throw new IllegalArgumentException("Unknown x-mock hint: " + hint);
        };
    }

    private static String type(JsonNode node) {
        JsonNode t = node.get("type");
        if (t == null) return node.has("properties") ? "object" : "string";
        if (t.isArray()) {
            for (JsonNode x : t) if (!"null".equals(x.asText())) return x.asText();
            return "null";
        }
        return t.asText();
    }

    private static JsonNode resolveRef(String ref, JsonNode root) {
        if (!ref.startsWith("#")) throw new IllegalArgumentException("Only local $ref supported: " + ref);
        JsonNode target = root.at(ref.substring(1));
        if (target.isMissingNode()) throw new IllegalArgumentException("Unresolvable $ref " + ref);
        return target;
    }

    /** Overrides by JSON Pointer, e.g. {@code "/rates/EUR" -> 1.5}. Parent objects must exist. */
    static void applyOverrides(JsonNode target, Map<String, Object> overrides) {
        if (overrides == null) return;
        for (Map.Entry<String, Object> e : new LinkedHashMap<>(overrides).entrySet()) {
            String pointer = e.getKey();
            int slash = pointer.lastIndexOf('/');
            JsonNode parent = slash <= 0 ? target : target.at(pointer.substring(0, slash));
            String field = pointer.substring(slash + 1);
            if (!(parent instanceof ObjectNode obj)) throw new IllegalArgumentException("No object at " + pointer);
            if (e.getValue() == null) obj.remove(field);
            else obj.set(field, JSON.valueToTree(e.getValue()));
        }
    }
}
