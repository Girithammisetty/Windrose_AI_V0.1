"use client";
import { Plus, Trash2 } from "lucide-react";
import { Input, Label } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import { useDatasets, flatten } from "@/lib/graphql/hooks";
import { newEntity } from "@/lib/semantic/definition";
import type { SemanticDefinitionDoc } from "@/lib/graphql/types";
import { t } from "@/lib/i18n/messages";

const SELECT_CLS = "h-9 w-full rounded-md border border-input bg-background px-2 text-sm";

/** wr:<tenant>:dataset:dataset/<id> -> the dataset id, for binding an entity's
 * dataset_urn from a picked dataset row. */
function urnFor(datasetId: string, sampleUrn: string | undefined): string {
  if (!sampleUrn) return "";
  const prefix = sampleUrn.slice(0, sampleUrn.lastIndexOf("/") + 1);
  return `${prefix}${datasetId}`;
}

export function EntitiesSection({
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
  const datasetsQuery = useDatasets();
  const datasets = flatten(datasetsQuery.data?.pages ?? []);

  const update = (i: number, patch: Partial<SemanticDefinitionDoc["entities"][number]>) => {
    const entities = doc.entities.map((e, idx) => (idx === i ? { ...e, ...patch } : e));
    onChange({ ...doc, entities });
  };
  const remove = (i: number) => onChange({ ...doc, entities: doc.entities.filter((_, idx) => idx !== i) });
  const add = () => onChange({ ...doc, entities: [...doc.entities, newEntity()] });

  return (
    <fieldset className="space-y-3" disabled={readOnly}>
      <div className="flex items-center justify-between">
        <legend className="text-sm font-semibold">{t("semantic.entities")}</legend>
        {!readOnly && (
          <Button type="button" variant="outline" size="sm" onClick={add}>
            <Plus /> {t("semantic.entity.add")}
          </Button>
        )}
      </div>

      {doc.entities.length === 0 && <p className="text-xs text-muted-foreground">{t("semantic.entity.none")}</p>}

      <div className="space-y-3">
        {doc.entities.map((entity, i) => {
          const rowErrors = errors.get(`entity/${entity.name}`) ?? [];
          const datasetId = entity.dataset_urn ? entity.dataset_urn.slice(entity.dataset_urn.lastIndexOf("/") + 1) : "";
          return (
            <div key={i} className="space-y-2 rounded-md border p-3" data-testid={`entity-row-${i}`}>
              <div className="grid gap-2 md:grid-cols-2">
                <div className="space-y-1">
                  <Label htmlFor={`entity-name-${i}`}>{t("semantic.entity.name")}</Label>
                  <Input
                    id={`entity-name-${i}`}
                    value={entity.name}
                    onChange={(e) => update(i, { name: e.target.value })}
                    placeholder="claims"
                  />
                </div>
                <div className="space-y-1">
                  <Label htmlFor={`entity-dataset-${i}`}>{t("semantic.entity.dataset")}</Label>
                  <select
                    id={`entity-dataset-${i}`}
                    className={SELECT_CLS}
                    value={datasetId}
                    onChange={(e) => {
                      const ds = datasets.find((d) => d.id === e.target.value);
                      if (!ds) return;
                      update(i, {
                        dataset_urn: urnFor(ds.id, ds.urn),
                        table: entity.table || `main.${ds.name.toLowerCase().replace(/[^a-z0-9_]+/g, "_")}`,
                      });
                    }}
                  >
                    <option value="">{t("semantic.entity.pickDataset")}</option>
                    {datasets.map((d) => (
                      <option key={d.id} value={d.id}>
                        {d.name}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="space-y-1">
                  <Label htmlFor={`entity-table-${i}`}>{t("semantic.entity.table")}</Label>
                  <Input
                    id={`entity-table-${i}`}
                    value={entity.table}
                    onChange={(e) => update(i, { table: e.target.value })}
                    placeholder="main.claims"
                  />
                </div>
                <div className="space-y-1">
                  <Label htmlFor={`entity-pk-${i}`}>{t("semantic.entity.primaryKey")}</Label>
                  <Input
                    id={`entity-pk-${i}`}
                    value={entity.primary_key.join(",")}
                    onChange={(e) =>
                      update(i, { primary_key: e.target.value.split(",").map((c) => c.trim()).filter(Boolean) })
                    }
                    placeholder="claim_id"
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
                    <Trash2 /> {t("semantic.entity.remove")}
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
