/**
 * Static, pre-execution query limits (BFF-FR-041, AC-5).
 *   max depth 10 · max aliases 20 · max root fields 5 · cost <= 5000.
 * List fields (those taking a `first` arg) cost `first x child-cost`.
 * A violation is a validation error with code QUERY_TOO_COMPLEX, so the
 * operation is rejected before any downstream call is made.
 */
import {
  type ValidationRule,
  type ValidationContext,
  type OperationDefinitionNode,
  type SelectionSetNode,
  type FragmentDefinitionNode,
  Kind,
  GraphQLError,
} from "graphql";
import type { Limits } from "../config.js";
import { ErrorCode } from "../errors/errors.js";

function tooComplex(msg: string): GraphQLError {
  return new GraphQLError(msg, { extensions: { code: ErrorCode.QUERY_TOO_COMPLEX } });
}

export function operationLimits(limits: Limits): ValidationRule {
  return (context: ValidationContext) => {
    const fragments = new Map<string, FragmentDefinitionNode>();
    for (const def of context.getDocument().definitions) {
      if (def.kind === Kind.FRAGMENT_DEFINITION) fragments.set(def.name.value, def);
    }

    let aliasCount = 0;

    function firstArg(node: { arguments?: readonly any[] }): number | undefined {
      const a = node.arguments?.find((x) => x.name.value === "first");
      if (!a) return undefined;
      if (a.value.kind === Kind.INT) return Number(a.value.value);
      return limits.defaultPageSize; // variable / non-literal -> assume default
    }

    // Returns [depth, cost] of a selection set. `seen` guards fragment cycles.
    function walk(sel: SelectionSetNode, seen: Set<string>): [number, number] {
      let maxDepth = 0;
      let cost = 0;
      for (const s of sel.selections) {
        if (s.kind === Kind.FIELD) {
          if (s.alias) aliasCount++;
          const child: [number, number] = s.selectionSet ? walk(s.selectionSet, seen) : [0, 0];
          const multiplier = firstArg(s) ?? 1;
          const fieldCost = 1 + child[1] * (multiplier > 0 ? multiplier : 1);
          cost += fieldCost;
          maxDepth = Math.max(maxDepth, 1 + child[0]);
        } else if (s.kind === Kind.INLINE_FRAGMENT && s.selectionSet) {
          const child = walk(s.selectionSet, seen);
          maxDepth = Math.max(maxDepth, child[0]);
          cost += child[1];
        } else if (s.kind === Kind.FRAGMENT_SPREAD) {
          const name = s.name.value;
          if (seen.has(name)) continue;
          const frag = fragments.get(name);
          if (frag) {
            const next = new Set(seen);
            next.add(name);
            const child = walk(frag.selectionSet, next);
            maxDepth = Math.max(maxDepth, child[0]);
            cost += child[1];
          }
        }
      }
      return [maxDepth, cost];
    }

    return {
      OperationDefinition(node: OperationDefinitionNode) {
        const rootFields = node.selectionSet.selections.filter((s) => s.kind === Kind.FIELD).length;
        if (rootFields > limits.maxRootFields) {
          context.reportError(tooComplex(`Too many root fields: ${rootFields} > ${limits.maxRootFields}`));
        }
        const [depth, cost] = walk(node.selectionSet, new Set());
        if (depth > limits.maxDepth) {
          context.reportError(tooComplex(`Query is too deep: ${depth} > ${limits.maxDepth}`));
        }
        if (cost > limits.maxCost) {
          context.reportError(tooComplex(`Query is too costly: ${cost} > ${limits.maxCost} points`));
        }
        if (aliasCount > limits.maxAliases) {
          context.reportError(tooComplex(`Too many aliases: ${aliasCount} > ${limits.maxAliases}`));
        }
      },
    };
  };
}
