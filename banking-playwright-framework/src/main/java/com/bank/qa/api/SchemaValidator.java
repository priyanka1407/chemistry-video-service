package com.bank.qa.api;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.networknt.schema.JsonMetaSchema;
import com.networknt.schema.JsonSchema;
import com.networknt.schema.JsonSchemaFactory;
import com.networknt.schema.NonValidationKeyword;
import com.networknt.schema.SpecVersion;
import com.networknt.schema.ValidationMessage;

import java.io.IOException;
import java.io.InputStream;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.stream.Collectors;

/**
 * JSON Schema (draft 2020-12) contract validation.
 *
 * <p>Used in two directions:
 * <ol>
 *   <li><b>Response contract tests</b> - the real API must satisfy the published schema.</li>
 *   <li><b>Mock drift protection</b> - every mocked payload served through {@code page.route()} is
 *       validated against the SAME schema, so a mock can never silently diverge from the contract
 *       (a classic cause of "green tests, broken production").</li>
 * </ol>
 * Schemas live in {@code src/test/resources/schemas} (in a real bank they would be pulled from the
 * API provider's OpenAPI spec / schema registry, ideally with consumer-driven contracts, e.g. Pact).
 */
public final class SchemaValidator {

    private static final ObjectMapper JSON = new ObjectMapper();
    /** Draft 2020-12 plus our vendor keyword "x-mock" (a generation hint, ignored by validation). */
    private static final JsonSchemaFactory FACTORY = JsonSchemaFactory.builder(JsonSchemaFactory.getInstance(SpecVersion.VersionFlag.V202012))
            .metaSchema(JsonMetaSchema.builder(JsonMetaSchema.getV202012()).keyword(new NonValidationKeyword("x-mock")).build())
            .build();
    private static final Map<String, JsonSchema> CACHE = new ConcurrentHashMap<>();
    private static final Map<String, JsonNode> RAW = new ConcurrentHashMap<>();

    private SchemaValidator() {}

    public static JsonNode schemaJson(String name) {
        return RAW.computeIfAbsent(name, n -> {
            try (InputStream in = SchemaValidator.class.getClassLoader().getResourceAsStream("schemas/" + n)) {
                if (in == null) throw new IllegalArgumentException("Schema not found: schemas/" + n);
                return JSON.readTree(in);
            } catch (IOException e) {
                throw new IllegalStateException(e);
            }
        });
    }

    /** Returns validation errors (empty = valid). */
    public static Set<String> validate(String schemaName, JsonNode instance) {
        JsonSchema schema = CACHE.computeIfAbsent(schemaName, n -> FACTORY.getSchema(schemaJson(n)));
        return schema.validate(instance).stream().map(ValidationMessage::getMessage).collect(Collectors.toSet());
    }

    public static void assertValid(String schemaName, JsonNode instance) {
        Set<String> errors = validate(schemaName, instance);
        if (!errors.isEmpty()) {
            throw new AssertionError("Payload violates " + schemaName + ": " + errors + "\nPayload: " + instance);
        }
    }
}
