import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";
import jsxA11y from "eslint-plugin-jsx-a11y";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  {
    rules: {
      // Accessibility: the full jsx-a11y recommended set (Next only enables a few).
      ...jsxA11y.flatConfigs.recommended.rules,

      // Errors go through lib/monitoring; stray logging does not ship.
      "no-console": ["error", { allow: ["warn", "error"] }],

      "@typescript-eslint/consistent-type-imports": [
        "error",
        { prefer: "type-imports", fixStyle: "inline-type-imports" },
      ],
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
      "no-restricted-syntax": [
        "error",
        {
          selector: "TSAsExpression[typeAnnotation.type='TSAnyKeyword']",
          message: "Avoid `as any`; narrow the type instead.",
        },
      ],
      // Security: no dynamic code execution, no raw HTML injection, no tabnabbing.
      "no-eval": "error",
      "no-implied-eval": "error",
      "no-new-func": "error",
      "no-script-url": "error",
      "react/no-danger": "error",
      "react/jsx-no-target-blank": ["error", { enforceDynamicLinks: "always" }],
      "react/jsx-no-script-url": "error",
      eqeqeq: ["error", "always", { null: "ignore" }],
      "prefer-const": "error",

      "import/order": [
        "error",
        {
          groups: ["builtin", "external", "internal", ["parent", "sibling", "index"]],
          pathGroups: [{ pattern: "@/**", group: "internal" }],
          pathGroupsExcludedImportTypes: ["builtin"],
          "newlines-between": "always",
          alphabetize: { order: "asc", caseInsensitive: true },
        },
      ],
    },
  },
  {
    // Tests and tooling may log and use looser patterns.
    files: ["**/*.test.{ts,tsx}", "src/test/**", "e2e/**", "*.config.*"],
    rules: { "no-console": "off", "no-restricted-syntax": "off" },
  },
  globalIgnores([
    ".next/**",
    ".next-e2e/**",
    "out/**",
    "build/**",
    "coverage/**",
    "playwright-report/**",
    "test-results/**",
    "next-env.d.ts",
    "src/lib/api/schema.d.ts",
  ]),
]);

export default eslintConfig;
