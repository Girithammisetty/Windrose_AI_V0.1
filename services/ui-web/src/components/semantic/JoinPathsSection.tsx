"use client";
import { Plus, Trash2 } from "lucide-react";
import { Input, Label } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import { JOIN_TYPES, CARDINALITIES, newJoinPath } from "@/lib/semantic/definition";
import type { SemanticDefinitionDoc } from "@/lib/graphql/types";
import { t } from "@/lib/i18n/messages";

const SELECT_CLS = "h-9 w-full rounded-md border border-input bg-background px-2 text-sm";

export function JoinPathsSection({
  doc,
  onChange,
  errors,
  readOnly,
}: {
  doc: SemanticDefinitionDoc;
  onChange: (doc: SemanticDefinitionDoc) => void;
  errors: Map<string, string[]>;
  readOnly: boolean;
}) {
  const update = (i: number, patch: Partial<SemanticDefinitionDoc["join_paths"][number]>) => {
    const join_paths = doc.join_paths.map((j, idx) => (idx === i ? { ...j, ...patch } : j));
    onChange({ ...doc, join_paths });
  };
  const remove = (i: number) => onChange({ ...doc, join_paths: doc.join_paths.filter((_, idx) => idx !== i) });
  const add = () => onChange({ ...doc, join_paths: [...doc.join_paths, newJoinPath()] });

  return (
    <fieldset className="space-y-3" disabled={readOnly}>
      <div className="flex items-center justify-between">
        <legend className="text-sm font-semibold">{t("semantic.joinPaths")}</legend>
        {!readOnly && (
          <Button type="button" variant="outline" size="sm" onClick={add} disabled={doc.entities.length < 2}>
            <Plus /> {t("semantic.joinPath.add")}
          </Button>
        )}
      </div>

      {doc.join_paths.length === 0 && <p className="text-xs text-muted-foreground">{t("semantic.joinPath.none")}</p>}

      <div className="space-y-3">
        {doc.join_paths.map((jp, i) => {
          const rowErrors = errors.get(`join_path/${jp.name}`) ?? [];
          const on = jp.on[0] ?? { from_column: "", to_column: "" };
          return (
            <div key={i} className="space-y-2 rounded-md border p-3" data-testid={`join-row-${i}`}>
              <div className="grid gap-2 md:grid-cols-3">
                <div className="space-y-1">
                  <Label htmlFor={`join-name-${i}`}>{t("semantic.joinPath.name")}</Label>
                  <Input id={`join-name-${i}`} value={jp.name} onChange={(e) => update(i, { name: e.target.value })} />
                </div>
                <div className="space-y-1">
                  <Label htmlFor={`join-from-${i}`}>{t("semantic.joinPath.from")}</Label>
                  <select
                    id={`join-from-${i}`}
                    className={SELECT_CLS}
                    value={jp.from_entity}
                    onChange={(e) => update(i, { from_entity: e.target.value })}
                  >
                    <option value="">—</option>
                    {doc.entities.map((en) => (
                      <option key={en.name} value={en.name}>
                        {en.name}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="space-y-1">
                  <Label htmlFor={`join-to-${i}`}>{t("semantic.joinPath.to")}</Label>
                  <select
                    id={`join-to-${i}`}
                    className={SELECT_CLS}
                    value={jp.to_entity}
                    onChange={(e) => update(i, { to_entity: e.target.value })}
                  >
                    <option value="">—</option>
                    {doc.entities.map((en) => (
                      <option key={en.name} value={en.name}>
                        {en.name}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="space-y-1">
                  <Label htmlFor={`join-type-${i}`}>{t("semantic.joinPath.type")}</Label>
                  <select
                    id={`join-type-${i}`}
                    className={SELECT_CLS}
                    value={jp.join_type}
                    onChange={(e) => update(i, { join_type: e.target.value })}
                  >
                    {JOIN_TYPES.map((jt) => (
                      <option key={jt} value={jt}>
                        {jt}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="space-y-1">
                  <Label htmlFor={`join-card-${i}`}>{t("semantic.joinPath.cardinality")}</Label>
                  <select
                    id={`join-card-${i}`}
                    className={SELECT_CLS}
                    value={jp.cardinality}
                    onChange={(e) => update(i, { cardinality: e.target.value })}
                  >
                    {CARDINALITIES.map((c) => (
                      <option key={c} value={c}>
                        {c}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              <div className="space-y-1">
                <Label>{t("semantic.joinPath.on")}</Label>
                <div className="flex items-center gap-2">
                  <Input
                    aria-label="from column"
                    value={on.from_column}
                    onChange={(e) => update(i, { on: [{ ...on, from_column: e.target.value }] })}
                    placeholder="policy_id"
                  />
                  <span className="text-muted-foreground">=</span>
                  <Input
                    aria-label="to column"
                    value={on.to_column}
                    onChange={(e) => update(i, { on: [{ ...on, to_column: e.target.value }] })}
                    placeholder="id"
                  />
                </div>
              </div>

              {rowErrors.length > 0 && (
                <ul className="space-y-0.5 text-xs text-destructive" role="alert">
                  {rowErrors.map((e, ei) => (
                    <li key={ei}>{e}</li>
                  ))}
                </ul>
              )}
              {!readOnly && (
                <div className="flex justify-end">
                  <Button type="button" variant="ghost" size="sm" onClick={() => remove(i)}>
                    <Trash2 /> {t("semantic.joinPath.remove")}
                  </Button>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </fieldset>
  );
}
