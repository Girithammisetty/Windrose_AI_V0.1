"use client";
import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { PageHeader } from "@/components/shell/PageHeader";
import { AsyncBoundary } from "@/components/primitives/AsyncBoundary";
import { Button } from "@/components/ui/button";
import { ConnectorTypePicker } from "@/components/connections/ConnectorTypePicker";
import { ConnectionForm } from "@/components/connections/ConnectionForm";
import { useConnectorTypes } from "@/lib/graphql/hooks";
import type { ConnectorType } from "@/lib/graphql/types";
import { FILE_UPLOAD_CONNECTOR } from "@/lib/connections/form";
import { t } from "@/lib/i18n/messages";

/** Synthetic picker tile: "File upload" is a data source too, but it bypasses the
 * connection form entirely — selecting it routes to the resumable upload wizard. */
const FILE_UPLOAD_TILE: ConnectorType = {
  connectorType: FILE_UPLOAD_CONNECTOR,
  displayName: t("upload.tile"),
  category: "file-upload",
  fields: [],
  secretFields: [],
  configSchema: {},
};

export default function NewConnectionPage() {
  const router = useRouter();
  const catalog = useConnectorTypes();
  const [selected, setSelected] = useState<ConnectorType | null>(null);

  // Prepend the file-upload tile to the bff-driven connector catalog.
  const types = useMemo(() => [FILE_UPLOAD_TILE, ...(catalog.data ?? [])], [catalog.data]);

  function onPick(type: ConnectorType) {
    if (type.connectorType === FILE_UPLOAD_CONNECTOR) {
      router.push("/data/upload");
      return;
    }
    setSelected(type);
  }

  return (
    <div>
      <PageHeader
        title={selected ? t("connections.configure", { type: selected.displayName }) : t("connections.pickType")}
        description={selected ? undefined : t("connections.pickTypeHint")}
        actions={
          <Button variant="ghost" size="sm" onClick={() => (selected ? setSelected(null) : router.push("/data/connections"))}>
            <ArrowLeft /> {selected ? t("connections.pickType") : t("connections.back")}
          </Button>
        }
      />

      <AsyncBoundary
        isLoading={catalog.isLoading}
        isError={catalog.isError}
        error={catalog.error}
        isEmpty={false}
        emptyTitle={t("connections.empty")}
        onRetry={() => catalog.refetch()}
      >
        {selected ? (
          <ConnectionForm
            type={selected}
            onSaved={() => router.push("/data/connections")}
            onCancel={() => setSelected(null)}
          />
        ) : (
          <ConnectorTypePicker types={types} onPick={onPick} />
        )}
      </AsyncBoundary>
    </div>
  );
}
