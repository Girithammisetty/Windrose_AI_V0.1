// Flat ESLint config (ESLint 9). Enforces TS hygiene and the BFF boundary:
// no business logic, no DB/Kafka clients (BFF-FR-003, BFF-FR-047 / master boundary).
import tseslint from "@typescript-eslint/eslint-plugin";
import tsparser from "@typescript-eslint/parser";
import noN1 from "./eslint-rules/no-n1-in-nested-resolver.mjs";

export default [
  {
    ignores: ["node_modules/**", "dist/**", "coverage/**"],
  },
  {
    files: ["src/**/*.ts", "tests/**/*.ts"],
    languageOptions: {
      parser: tsparser,
      parserOptions: { ecmaVersion: 2022, sourceType: "module" },
    },
    plugins: { "@typescript-eslint": tseslint },
    rules: {
      ...tseslint.configs.recommended.rules,
      "@typescript-eslint/no-explicit-any": "off",
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
      // The BFF is a stateless aggregation layer: it must not reach a DB, a
      // broker, or proxy a stream. Importing these is an architecture violation.
      "no-restricted-imports": [
        "error",
        {
          paths: [
            { name: "pg", message: "BFF holds no DB (BFF-FR-003 / BRD §4)." },
            { name: "kafkajs", message: "BFF emits/consumes no events (BRD §6)." },
            { name: "ioredis", message: "No tenant data at rest in the BFF (BRD §4)." },
          ],
        },
      ],
    },
  },
  {
    // N+1 fan-out guard (BFF-FR-030), applied to the resolver definitions.
    files: ["src/resolvers/**/*.ts"],
    plugins: { bff: { rules: { "no-n1-in-nested-resolver": noN1 } } },
    rules: { "bff/no-n1-in-nested-resolver": "error" },
  },
];
