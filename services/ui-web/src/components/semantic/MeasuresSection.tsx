"use client";
import { Plus, Trash2 } from "lucide-react";
import { Input, Label } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import { AGG_FNS, EXPR_FUNCS, newMeasure } from "@/lib/semantic/definition";
import type { SemanticDefinitionDoc, DatasetColumn } from "@/lib/graphql/types";
import { t } from "@/lib/i18n/messages";

const SELECT_CLS = "h-9 w-full rounded-md border border-input bg-background px-2 text-sm";

export function MeasuresSection({
  doc,
  onChange,
  columnsByEntity,
  errors,
  readOnly,
}: {
  doc: SemanticDefinitionDoc;
  onChange: (doc: SemanticDefinitionDoc) => void;
  columnsByEntity: Record<string, DatasetColumn[]>;
  errors: Map<string, string[]>;
  readOnly: boolean;
}) {
  const update = (i: number, patch: Partial<SemanticDefinitionDoc["measures"][number]>) => {
    const measures = doc.measures.map((m, idx) => (idx === i ? { ...m, ...patch } : m));
    onChange({ ...doc, measures });
  };
  const remove = (i: number) => onChange({ ...doc, measures: doc.measures.filter((_, idx) => idx !== i) });
  const add = () => onChange({ ...doc, measures: [...doc.measures, newMeasure(doc.entities[0]?.name)] });

  return (
    <fieldset className="space-y-3" disabled={readOnly}>
      <div className="flex items-center justify-between">
        <legend className="text-sm font-semibold">{t("semantic.measures")}</legend>
        {!readOnly && (
          <Button type="button" variant="outline" size="sm" onClick={add} disabled={doc.entities.length === 0}>
            <Plus /> {t("semantic.measure.add")}
          </Button>
        )}
      </div>

      {doc.measures.length === 0 && <p className="text-xs text-muted-foreground">{t("semantic.measure.none")}</p>}

      <div className="space-y-3">
        {doc.measures.map((meas, i) => {
          const rowErrors = errors.get(`measure/${meas.name}`) ?? [];
          const columns = columnsByEntity[meas.entity ?? ""] ?? [];
          const isDerived = meas.expr_metric != null;
          return (
            <div key={i} className="space-y-2 rounded-md border p-3" data-testid={`measure-row-${i}`}>
              <div className="grid gap-2 md:grid-cols-3">
                <div className="space-y-1">
                  <Label htmlFor={`meas-name-${i}`}>{t("semantic.measure.name")}</Label>
                  <Input id={`meas-name-${i}`} value={meas.name} onChange={(e) => update(i, { name: e.target.value })} />
                </div>
                <div className="space-y-1">
                  <Label htmlFor={`meas-entity-${i}`}>{t("semantic.measure.entity")}</Label>
                  <select
                    id={`meas-entity-${i}`}
                    className={SELECT_CLS}
                    value={meas.entity ?? ""}
                    disabled={isDerived}
                    onChange={(e) => update(i, { entity: e.target.value })}
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
                  <Label htmlFor={`meas-agg-${i}`}>{t("semantic.measure.agg")}</Label>
                  <select
                    id={`meas-agg-${i}`}
                    className={SELECT_CLS}
                    value={meas.agg ?? ""}
                    disabled={isDerived}
                    onChange={(e) => update(i, { agg: e.target.value })}
                  >
                    {AGG_FNS.map((a) => (
                      <option key={a} value={a}>
                        {a}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              {meas.agg === "count" && !isDerived ? (
                <p className="text-xs text-muted-foreground">count() with no expr counts all rows (count(*)).</p>
              ) : !isDerived ? (
                <div className="space-y-1">
                  <Label htmlFor={`meas-expr-${i}`}>{t("semantic.measure.expr")}</Label>
                  {columns.length > 0 ? (
                    <select
                      id={`meas-expr-${i}`}
                      className={SELECT_CLS}
                      value={meas.expr ?? ""}
                      onChange={(e) => update(i, { expr: e.target.value })}
                    >
                      <option value="">{t("semantic.dimension.pickColumn")}</option>
                      {columns.map((c) => (
                        <option key={c.name} value={c.name}>
                          {c.name}
                          {c.type ? ` (${c.type})` : ""}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <Input id={`meas-expr-${i}`} value={meas.expr ?? ""} onChange={(e) => update(i, { expr: e.target.value })} />
                  )}
                  <p className="text-xs text-muted-foreground">
                    {t("semantic.measure.exprHint")} {EXPR_FUNCS.join(", ")}.
                  </p>
                </div>
              ) : (
                <div className="space-y-1">
                  <Label htmlFor={`meas-derived-${i}`}>Derived from</Label>
                  <Input
                    id={`meas-derived-${i}`}
                    value={meas.expr_metric ?? ""}
                    onChange={(e) => update(i, { expr_metric: e.target.value })}
                    placeholder="claim_count / policy_count"
                  />
                  <p className="text-xs text-muted-foreground">
                    Combine other measure names with + - * / and nullif() only.
                  </p>
                </div>
              )}

              {!isDerived && (
                <div className="space-y-1">
                  <Label htmlFor={`meas-filter-${i}`}>{t("semantic.measure.filters")}</Label>
                  <Input
                    id={`meas-filter-${i}`}
                    value={meas.filters ?? ""}
                    onChange={(e) => update(i, { filters: e.target.value || undefined })}
                    placeholder="claim_type = 'auto'"
                  />
                </div>
              )}

              <div className="space-y-1">
                <Label htmlFor={`meas-format-${i}`}>{t("semantic.measure.format")}</Label>
                <Input
                  id={`meas-format-${i}`}
                  value={meas.format ?? ""}
                  onChange={(e) => update(i, { format: e.target.value || undefined })}
                  placeholder="currency | percent | number"
                />
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
                    <Trash2 /> {t("semantic.measure.remove")}
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
