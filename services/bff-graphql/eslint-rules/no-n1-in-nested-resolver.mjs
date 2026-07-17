/**
 * Custom lint rule enforcing BFF-FR-030 (N+1 protection).
 *
 * A field resolver on an ENTITY type (Case, Dataset, Experiment, Run, ...) that
 * hydrates a related resource by calling a downstream client directly
 * (`ctx.clients.*`) — instead of going through a per-request dataloader
 * (`ctx.loaders.*`) — is an N+1 fan-out: it fires one downstream call per parent
 * when the parent appears in a list/connection. Such resolvers MUST batch via a
 * dataloader.
 *
 * This is the guard that would have caught the `Experiment.runs` /  `Run.model`
 * regression. Escape hatch: a resolver may be annotated `// n1-safe: <reason>`
 * when it batches by another mechanism (documented).
 */

/** Entity resolver blocks whose nested fields must batch through loaders. */
const WATCHED_TYPES = new Set(["Case", "Dataset", "Experiment", "Run", "Proposal", "AgentRun", "User"]);

function keyName(node) {
  if (!node) return undefined;
  return node.name ?? node.value;
}

/** @type {import('eslint').Rule.RuleModule} */
const rule = {
  meta: {
    type: "problem",
    docs: {
      description:
        "Nested entity resolvers must hydrate related resources via a dataloader, not a per-item downstream client call (BFF-FR-030).",
    },
    schema: [],
    messages: {
      n1: "N+1 fan-out: {{type}}.{{field}} calls ctx.clients.* directly. Hydrate through ctx.loaders.* (BFF-FR-030), or annotate `// n1-safe: <reason>` if batched elsewhere.",
    },
  },
  create(context) {
    const sourceCode = context.sourceCode ?? context.getSourceCode();
    return {
      Property(node) {
        // Is this property a field resolver (a function) inside a watched type block?
        const value = node.value;
        if (
          !value ||
          (value.type !== "ArrowFunctionExpression" && value.type !== "FunctionExpression")
        ) {
          return;
        }
        const block = node.parent; // ObjectExpression of the type's resolvers
        const typeProp = block && block.parent; // Property `TypeName: { ... }`
        if (!typeProp || typeProp.type !== "Property") return;
        const typeName = keyName(typeProp.key);
        if (!typeName || !WATCHED_TYPES.has(typeName)) return;

        const text = sourceCode.getText(value);
        const commentsBefore = sourceCode.getCommentsBefore(node);
        const hasEscape = commentsBefore.some((c) => /n1-safe/.test(c.value));
        if (hasEscape) return;

        const callsClient = /\bclients\s*\./.test(text);
        const usesLoader = /\bloaders\s*\./.test(text);
        if (callsClient && !usesLoader) {
          context.report({
            node,
            messageId: "n1",
            data: { type: typeName, field: keyName(node.key) ?? "?" },
          });
        }
      },
    };
  },
};

export default rule;
