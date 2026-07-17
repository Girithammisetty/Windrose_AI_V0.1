"use client";
import { Plus, Trash2 } from "lucide-react";
import { Input, Label } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import { DIM_TYPES, TIME_GRAINS, EXPR_FUNCS, newDimension } from "@/lib/semantic/definition";
import type { SemanticDefinitionDoc, DatasetColumn } from "@/lib/graphql/types";
import { t } from "@/lib/i18n/messages";

const SELECT_CLS = "h-9 w-full rounded-md border border-input bg-background px-2 text-sm";

export function DimensionsSection({
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
  const update = (i: number, patch: Partial<SemanticDefinitionDoc["dimensions"][number]>) => {
    const dimensions = doc.dimensions.map((d, idx) => (idx === i ? { ...d, ...patch } : d));
    onChange({ ...doc, dimensions });
  };
  const remove = (i: number) => onChange({ ...doc, dimensions: doc.dimensions.filter((_, idx) => idx !== i) });
  const add = () => onChange({ ...doc, dimensions: [...doc.dimensions, newDimension(doc.entities[0]?.name)] });

  return (
    <fieldset className="space-y-3" disabled={readOnly}>
      <div className="flex items-center justify-between">
        <legend className="text-sm font-semibold">{t("semantic.dimensions")}</legend>
        {!readOnly && (
          <Button type="button" variant="outline" size="sm" onClick={add} disabled={doc.entities.length === 0}>
            <Plus /> {t("semantic.dimension.add")}
          </Button>
        )}
      </div>

      {doc.dimensions.length === 0 && <p className="text-xs text-muted-foreground">{t("semantic.dimension.none")}</p>}

      <div className="space-y-3">
        {doc.dimensions.map((dim, i) => {
          const rowErrors = errors.get(`dimension/${dim.name}`) ?? [];
          const columns = columnsByEntity[dim.entity] ?? [];
          const mode = dim.expr ? "expr" : "column";
          return (
            <div key={i} className="space-y-2 rounded-md border p-3" data-testid={`dimension-row-${i}`}>
              <div className="grid gap-2 md:grid-cols-3">
                <div className="space-y-1">
                  <Label htmlFor={`dim-name-${i}`}>{t("semantic.dimension.name")}</Label>
                  <Input id={`dim-name-${i}`} value={dim.name} onChange={(e) => update(i, { name: e.target.value })} />
                </div>
                <div className="space-y-1">
                  <Label htmlFor={`dim-entity-${i}`}>{t("semantic.dimension.entity")}</Label>
                  <select
                    id={`dim-entity-${i}`}
                    className={SELECT_CLS}
                    value={dim.entity}
                    onChange={(e) => update(i, { entity: e.target.value })}
                  >
                    {doc.entities.map((en) => (
                      <option key={en.name} value={en.name}>
                        {en.name}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="space-y-1">
                  <Label htmlFor={`dim-type-${i}`}>{t("semantic.dimension.type")}</Label>
                  <select
                    id={`dim-type-${i}`}
                    className={SELECT_CLS}
                    value={dim.type}
                    onChange={(e) => update(i, { type: e.target.value })}
                  >
                    {DIM_TYPES.map((dt) => (
                      <option key={dt} value={dt}>
                        {dt}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              <div className="flex items-center gap-3 text-xs">
                <label className="flex items-center gap-1">
                  <input
                    type="radio"
                    name={`dim-mode-${i}`}
                    checked={mode === "column"}
                    onChange={() => update(i, { column: "", expr: undefined })}
                  />
                  {t("semantic.dimension.mode.column")}
                </label>
                <label className="flex items-center gap-1">
                  <input
                    type="radio"
                    name={`dim-mode-${i}`}
                    checked={mode === "expr"}
                    onChange={() => update(i, { column: undefined, expr: "" })}
                  />
                  {t("semantic.dimension.mode.expr")}
                </label>
              </div>

              {mode === "column" ? (
                <div className="space-y-1">
                  <Label htmlFor={`dim-column-${i}`}>{t("semantic.dimension.column")}</Label>
                  <select
                    id={`dim-column-${i}`}
                    className={SELECT_CLS}
                    value={dim.column ?? ""}
                    onChange={(e) => update(i, { column: e.target.value })}
                  >
                    <option value="">{t("semantic.dimension.pickColumn")}</option>
                    {columns.map((c) => (
                      <option key={c.name} value={c.name}>
                        {c.name}
                        {c.type ? ` (${c.type})` : ""}
                      </option>
                    ))}
                  </select>
                </div>
              ) : (
                <div className="space-y-1">
                  <Label htmlFor={`dim-expr-${i}`}>{t("semantic.dimension.expr")}</Label>
                  <Input
                    id={`dim-expr-${i}`}
                    value={dim.expr ?? ""}
                    onChange={(e) => update(i, { expr: e.target.value })}
                    placeholder={`lower(${columns[0]?.name ?? "column"})`}
                  />
                  <p className="text-xs text-muted-foreground">
                    {t("semantic.dimension.exprHint")} {EXPR_FUNCS.join(", ")}.
                  </p>
                </div>
              )}

              {dim.type === "time" && (
                <div className="space-y-1">
                  <Label>{t("semantic.dimension.timeGrains")}</Label>
                  <div className="flex flex-wrap gap-2">
                    {TIME_GRAINS.map((g) => (
                      <label key={g} className="flex items-center gap-1 text-xs">
                        <input
                          type="checkbox"
                          checked={(dim.time_grains ?? []).includes(g)}
                          onChange={(e) => {
                            const set = new Set(dim.time_grains ?? []);
                            if (e.target.checked) set.add(g);
                            else set.delete(g);
                            update(i, { time_grains: Array.from(set) });
                          }}
                        />
                        {g}
                      </label>
                    ))}
                  </div>
                </div>
              )}

              <div className="space-y-1">
                <Label htmlFor={`dim-syn-${i}`}>{t("semantic.dimension.synonyms")}</Label>
                <Input
                  id={`dim-syn-${i}`}
                  value={(dim.synonyms ?? []).join(", ")}
                  onChange={(e) =>
                    update(i, { synonyms: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })
                  }
                  placeholder={t("semantic.dimension.synonymsPlaceholder")}
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
                    <Trash2 /> {t("semantic.dimension.remove")}
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
