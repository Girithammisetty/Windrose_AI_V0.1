"use client";
import { useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowLeft, UploadCloud, CheckCircle2, AlertTriangle, Loader2 } from "lucide-react";
import { PageHeader } from "@/components/shell/PageHeader";
import { Can } from "@/components/authz/Can";
import { Card, CardContent, Input, Label } from "@/components/ui/primitives";
import { Button } from "@/components/ui/button";
import { FileDropzone } from "@/components/uploads/FileDropzone";
import { FormatPicker } from "@/components/uploads/FormatPicker";
import { FEATURE_GATES } from "@/lib/authz/registry";
import {
  useCreateIngestion,
  useCreateUpload,
  useCompleteUpload,
  useIngestion,
  useDataset,
  useDatasetSchema,
} from "@/lib/graphql/hooks";
import { chunkFile, putUploadPart, baseName, type UploadedPart } from "@/lib/uploads/chunk";
import { detectUploadFormat, uploadFormat, formatBytes } from "@/lib/uploads/formats";
import { t } from "@/lib/i18n/messages";

/** Requested chunk size (8MB). ingestion-service may echo back a different
 * part_size on createUpload — the actual split always follows the server's value. */
const DEFAULT_CHUNK_SIZE = 8 * 1024 * 1024;

type Step = "select" | "upload" | "review";
type Phase = "idle" | "creating" | "uploading" | "completing" | "done" | "error";

const STEP_LABELS: Record<Step, string> = {
  select: t("upload.step.select"),
  upload: t("upload.step.upload"),
  review: t("upload.step.review"),
};

/** dataset_urn = wr:{tenant}:dataset:dataset/{dataset_id} -> the id is the last segment. */
function datasetIdFromUrn(urn: string | null | undefined): string {
  if (!urn) return "";
  return urn.split("/").pop() ?? "";
}

export default function DataUploadPage() {
  const router = useRouter();

  const [step, setStep] = useState<Step>("select");
  const [file, setFile] = useState<File | null>(null);
  const [format, setFormat] = useState<string | null>(null);
  const [datasetName, setDatasetName] = useState("");

  const [phase, setPhase] = useState<Phase>("idle");
  const [error, setError] = useState<string | null>(null);
  const [uploadId, setUploadId] = useState<string | null>(null);
  const [chunks, setChunks] = useState<Blob[]>([]);
  const [confirmedParts, setConfirmedParts] = useState<UploadedPart[]>([]);
  const [ingestionId, setIngestionId] = useState<string | null>(null);
  const [datasetUrn, setDatasetUrn] = useState<string | null>(null);

  const createIngestion = useCreateIngestion();
  const createUpload = useCreateUpload();
  const completeUpload = useCompleteUpload();

  const totalParts = chunks.length;
  const progressPct = totalParts > 0 ? Math.round((confirmedParts.length / totalParts) * 100) : 0;
  const pending = phase === "creating" || phase === "uploading" || phase === "completing";

  /** On file pick, auto-detect the format from the extension and suggest a name. */
  function onFile(f: File | null) {
    setFile(f);
    if (f) {
      const detected = detectUploadFormat(f.name);
      if (detected) setFormat(detected);
      if (!datasetName.trim()) setDatasetName(baseName(f.name));
    }
  }

  function reset() {
    setStep("select");
    setFile(null);
    setFormat(null);
    setDatasetName("");
    setPhase("idle");
    setError(null);
    setUploadId(null);
    setChunks([]);
    setConfirmedParts([]);
    setIngestionId(null);
    setDatasetUrn(null);
  }

  async function uploadChunksFrom(uid: string, from: number, existing: UploadedPart[], all: Blob[]) {
    const parts = [...existing];
    for (let i = from; i < all.length; i++) {
      const part = await putUploadPart(uid, i + 1, all[i]);
      parts.push(part);
      setConfirmedParts([...parts]);
    }
    return parts;
  }

  async function finish(uid: string, parts: UploadedPart[]) {
    setPhase("completing");
    const r = await completeUpload.mutateAsync({
      uploadId: uid,
      input: { parts: parts.map((p) => ({ n: p.n, etag: p.etag, size: p.size })) },
    });
    setDatasetUrn(r.completeUpload.datasetUrn ?? datasetUrn);
    setPhase("done");
    setStep("review");
  }

  async function startUpload() {
    if (!file || !format) return;
    setError(null);
    setStep("upload");
    setPhase("creating");
    try {
      const ingestionResult = await createIngestion.mutateAsync({
        mode: "file_upload",
        fileFormat: format,
        newDatasetName: datasetName.trim() || baseName(file.name),
      });
      const ingestion = ingestionResult.createIngestion;
      setIngestionId(ingestion.id);
      setDatasetUrn(ingestion.datasetUrn ?? null);

      const uploadResult = await createUpload.mutateAsync({
        ingestionId: ingestion.id,
        partSize: DEFAULT_CHUNK_SIZE,
        bytesTotal: file.size,
      });
      const upload = uploadResult.createUpload;
      const effectivePartSize = upload.partSize ?? DEFAULT_CHUNK_SIZE;
      const newChunks = chunkFile(file, effectivePartSize);

      setUploadId(upload.uploadId);
      setChunks(newChunks);
      setConfirmedParts([]);
      setPhase("uploading");

      const parts = await uploadChunksFrom(upload.uploadId, 0, [], newChunks);
      await finish(upload.uploadId, parts);
    } catch (e) {
      setError(e instanceof Error ? e.message : "upload failed");
      setPhase("error");
    }
  }

  async function retry() {
    if (!uploadId) return;
    setError(null);
    setPhase("uploading");
    try {
      const parts = await uploadChunksFrom(uploadId, confirmedParts.length, confirmedParts, chunks);
      await finish(uploadId, parts);
    } catch (e) {
      setError(e instanceof Error ? e.message : "upload failed");
      setPhase("error");
    }
  }

  const canStart = !!file && !!format && !pending;
  const fmt = uploadFormat(format);

  return (
    <div>
      <PageHeader
        title={t("upload.title")}
        description={t("upload.subtitle")}
        actions={
          <Button variant="ghost" size="sm" onClick={() => router.push("/data/connections")}>
            <ArrowLeft /> {t("upload.backToSources")}
          </Button>
        }
      />

      {/* Stepper */}
      <ol className="mb-4 flex items-center gap-2 text-xs text-muted-foreground" aria-label="Upload steps">
        {(["select", "upload", "review"] as Step[]).map((s, i) => (
          <li key={s} className="flex items-center gap-2" aria-current={step === s ? "step" : undefined}>
            <span
              className={
                "flex size-5 items-center justify-center rounded-full text-[10px] font-semibold " +
                (step === s ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground")
              }
            >
              {i + 1}
            </span>
            <span className={step === s ? "font-medium text-foreground" : ""}>{STEP_LABELS[s]}</span>
            {i < 2 && <span className="text-muted-foreground/40">→</span>}
          </li>
        ))}
      </ol>

      <Card>
        <CardContent className="space-y-5 pt-5">
          {step === "select" && (
            <>
              <div className="space-y-2">
                <Label>{t("upload.fileLabel")}</Label>
                <FileDropzone file={file} onFile={onFile} />
              </div>

              <div className="space-y-2">
                <Label>{t("upload.formatLabel")}</Label>
                <FormatPicker value={format} onChange={setFormat} />
                {fmt && <p className="text-xs text-muted-foreground">{fmt.hint}</p>}
              </div>

              <div className="space-y-1">
                <Label htmlFor="upload-dataset-name">{t("upload.datasetNameLabel")}</Label>
                <Input
                  id="upload-dataset-name"
                  value={datasetName}
                  onChange={(e) => setDatasetName(e.target.value)}
                  placeholder={file ? baseName(file.name) : "new-dataset"}
                  aria-label={t("upload.datasetNameLabel")}
                  className="max-w-sm"
                />
              </div>

              <div className="flex items-center gap-3">
                <Can
                  gate={FEATURE_GATES.createUpload}
                  fallback={<p className="text-xs text-muted-foreground">{t("upload.noPermission")}</p>}
                >
                  <Button onClick={startUpload} disabled={!canStart}>
                    <UploadCloud /> {t("upload.start")}
                  </Button>
                </Can>
                {file && !format && (
                  <p className="text-xs text-muted-foreground">{t("upload.pickFormatHint")}</p>
                )}
              </div>
            </>
          )}

          {step === "upload" && (
            <UploadProgress
              phase={phase}
              file={file}
              confirmedCount={confirmedParts.length}
              totalParts={totalParts}
              progressPct={progressPct}
              error={error}
              uploadId={uploadId}
              onRetry={retry}
              onBack={reset}
            />
          )}

          {step === "review" && (
            <ReviewStep
              ingestionId={ingestionId}
              datasetId={datasetIdFromUrn(datasetUrn)}
              datasetName={datasetName.trim() || (file ? baseName(file.name) : "")}
              format={format}
              onReset={reset}
            />
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function UploadProgress({
  phase,
  file,
  confirmedCount,
  totalParts,
  progressPct,
  error,
  uploadId,
  onRetry,
  onBack,
}: {
  phase: Phase;
  file: File | null;
  confirmedCount: number;
  totalParts: number;
  progressPct: number;
  error: string | null;
  uploadId: string | null;
  onRetry: () => void;
  onBack: () => void;
}) {
  const stepLabel = useMemo(() => {
    switch (phase) {
      case "creating":
        return t("upload.phase.creating");
      case "uploading":
        return t("upload.phase.uploading", { n: Math.min(confirmedCount + 1, totalParts), total: totalParts });
      case "completing":
        return t("upload.phase.completing");
      default:
        return "";
    }
  }, [phase, confirmedCount, totalParts]);

  return (
    <div className="space-y-3" data-upload-phase={phase}>
      {file && (
        <p className="text-sm font-medium">
          {file.name} · {formatBytes(file.size)}
        </p>
      )}
      {phase !== "error" && <p className="text-sm text-muted-foreground">{stepLabel}</p>}
      <div
        className="h-2 overflow-hidden rounded-full bg-muted"
        role="progressbar"
        aria-valuenow={progressPct}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div className="h-full bg-primary" style={{ width: `${progressPct}%` }} />
      </div>
      {totalParts > 0 && (
        <p className="text-xs text-muted-foreground">
          {confirmedCount}/{totalParts} {t("upload.chunksConfirmed")} ({progressPct}%)
        </p>
      )}

      {phase === "error" && error && (
        <div className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
          <div>
            <p>{error}</p>
            {uploadId && (
              <p className="mt-1 text-xs">
                {t("upload.resumable", { done: confirmedCount, total: totalParts })}
              </p>
            )}
            <div className="mt-2 flex gap-2">
              {uploadId && (
                <Button size="sm" onClick={onRetry}>
                  {t("upload.resume")}
                </Button>
              )}
              <Button size="sm" variant="outline" onClick={onBack}>
                {t("upload.startOver")}
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function ReviewStep({
  ingestionId,
  datasetId,
  datasetName,
  format,
  onReset,
}: {
  ingestionId: string | null;
  datasetId: string;
  datasetName: string;
  format: string | null;
  onReset: () => void;
}) {
  const ingestion = useIngestion(ingestionId);
  const status = ingestion.data?.status ?? "queued";
  const failed = status === "failed" || status === "cancelled" || status === "expired";
  const completed = status === "completed";

  const schema = useDatasetSchema(datasetId, undefined, { enabled: completed && !!datasetId, poll: true });
  const dataset = useDataset(completed && datasetId ? datasetId : "");
  const columns = schema.data ?? [];
  const rowCount = dataset.data?.dataset?.rowCount ?? ingestion.data?.rowsAppended ?? null;

  return (
    <div className="space-y-4" data-review-status={status}>
      <div className="flex flex-wrap items-center gap-x-6 gap-y-1 text-sm">
        <span>
          <span className="text-muted-foreground">{t("upload.review.dataset")}:</span>{" "}
          <span className="font-medium">{datasetName}</span>
        </span>
        <span>
          <span className="text-muted-foreground">{t("upload.review.format")}:</span>{" "}
          <span className="font-mono text-xs uppercase">{format}</span>
        </span>
        <span>
          <span className="text-muted-foreground">{t("upload.review.status")}:</span>{" "}
          <span className="font-medium">{status}</span>
        </span>
      </div>

      {failed && (
        <div className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
          <p>{t("upload.review.failed")}</p>
        </div>
      )}

      {!failed && !completed && (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" aria-hidden />
          <span>{t("upload.review.processing")}</span>
        </div>
      )}

      {completed && (
        <div className="space-y-3">
          <div className="flex items-start gap-2 rounded-md border border-[hsl(var(--success))]/40 bg-[hsl(var(--success))]/5 p-3 text-sm">
            <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-[hsl(var(--success))]" aria-hidden />
            <p>
              {t("upload.review.done", { rows: rowCount ?? "…", cols: columns.length })}
            </p>
          </div>

          <div>
            <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              {t("upload.review.schema")}
            </p>
            {columns.length === 0 ? (
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="size-4 animate-spin" aria-hidden />
                <span>{t("upload.review.inferringSchema")}</span>
              </div>
            ) : (
              <div className="overflow-x-auto rounded-md border">
                <table className="w-full text-sm" data-testid="upload-schema-preview">
                  <thead className="bg-muted/50 text-left text-xs text-muted-foreground">
                    <tr>
                      <th className="px-3 py-2 font-medium">{t("upload.review.column")}</th>
                      <th className="px-3 py-2 font-medium">{t("upload.review.type")}</th>
                      <th className="px-3 py-2 font-medium">{t("upload.review.nullable")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {columns.map((c) => (
                      <tr key={c.name} className="border-t">
                        <td className="px-3 py-1.5 font-mono text-xs">{c.name}</td>
                        <td className="px-3 py-1.5">{c.type}</td>
                        <td className="px-3 py-1.5 text-muted-foreground">{c.nullable ? "—" : "not null"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}

      <div className="flex gap-2">
        {completed && datasetId && (
          <Button asChild>
            <Link href={`/data/datasets/${datasetId}`}>{t("upload.review.openDataset")}</Link>
          </Button>
        )}
        <Button variant="outline" onClick={onReset}>
          {t("upload.review.uploadAnother")}
        </Button>
      </div>
    </div>
  );
}
