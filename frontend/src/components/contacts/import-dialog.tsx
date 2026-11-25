"use client";

import { Upload } from "lucide-react";
import { useRef, useState } from "react";
import { toast } from "sonner";

import { Dialog } from "@/components/ui/overlay";
import { Button, Field, Select } from "@/components/ui/primitives";
import { FormError } from "@/components/workspace/list-shared";
import { useContactFields, useImportContacts } from "@/hooks/use-contacts";
import { ApiError } from "@/lib/api/client";
import type { ContactImportResult, ImportMapping, OnExisting } from "@/lib/api/contacts-types";
import { useFormik, yup } from "@/lib/forms";

const MAX_BYTES = 2 * 1024 * 1024;

const PROPS = [
  { key: "email", label: "Email", required: true },
  { key: "name", label: "Name" },
  { key: "phone", label: "Phone" },
  { key: "organization", label: "Organization" },
  { key: "vip", label: "VIP" },
] as const;

const EXISTING: { value: OnExisting; label: string }[] = [
  { value: "fill_blanks", label: "Fill empty values only" },
  { value: "overwrite", label: "Overwrite with the file's values" },
  { value: "skip", label: "Skip" },
];

/** One dropdown value per CeDesk property (`email`, `attr:plan`); "" means don't import. */
type Picks = Record<string, string>;

export function picksFrom(m: ImportMapping): Picks {
  const out: Picks = {};
  for (const p of PROPS) if (m[p.key]) out[p.key] = m[p.key]!;
  for (const [k, col] of Object.entries(m.attributes ?? {})) if (col) out[`attr:${k}`] = col;
  return out;
}

export function mappingFrom(p: Picks): ImportMapping {
  const m: ImportMapping = {};
  const attributes: Record<string, string> = {};
  for (const [k, col] of Object.entries(p)) {
    if (!col) continue;
    if (k.startsWith("attr:")) attributes[k.slice(5)] = col;
    else m[k as (typeof PROPS)[number]["key"]] = col;
  }
  if (Object.keys(attributes).length) m.attributes = attributes;
  return m;
}

const msg = (e: unknown) => (e instanceof ApiError ? e.userMessage : "Something went wrong.");

export function ImportDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Import customers from CSV"
      description="UTF-8, with a header row. At most 2 MB and 5,000 rows."
      className="sm:max-w-2xl"
    >
      {open && <ImportSteps onClose={onClose} />}
    </Dialog>
  );
}

function Numbers({ r }: { r: ContactImportResult }) {
  return (
    <p className="text-sm">
      <strong>{r.rows}</strong> rows: {r.created} new, {r.updated} updated, {r.unchanged} unchanged,{" "}
      {r.error_count} with problems
    </p>
  );
}

function Problems({ r }: { r: ContactImportResult }) {
  if (r.errors.length === 0) return null;
  return (
    <div>
      <div className="max-h-48 overflow-auto rounded-lg border border-border">
        <table className="w-full text-left text-xs">
          <thead className="sticky top-0 bg-surface-2 text-muted-foreground">
            <tr>
              <th className="px-3 py-1.5 font-medium">Line</th>
              <th className="px-3 py-1.5 font-medium">Problem</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {r.errors.map((e, i) => (
              <tr key={`${e.line}-${i}`}>
                <td className="px-3 py-1.5 tabular-nums">{e.line}</td>
                <td className="px-3 py-1.5">{e.reason}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {r.error_count > 200 && (
        <p className="mt-1 text-xs text-muted-foreground">Showing the first 200.</p>
      )}
    </div>
  );
}

interface ImportValues {
  file: File | null;
  picks: Picks;
  onExisting: OnExisting;
}

const importSchema = yup.object({
  file: yup
    .mixed<File>()
    .nullable()
    .required("Choose a CSV file.")
    .test("size", "That file is over 2 MB. Split it and import the parts.", (f) =>
      f ? f.size <= MAX_BYTES : true,
    )
    .test("type", "Choose a .csv file.", (f) =>
      f ? /\.csv$/i.test(f.name) || f.type === "text/csv" : true,
    ),
  picks: yup
    .object()
    .test("email", "Choose the column that holds the email.", (p) => !!(p as Picks)?.email),
  onExisting: yup.mixed<OnExisting>().oneOf(["fill_blanks", "overwrite", "skip"]).required(),
});

function ImportSteps({ onClose }: { onClose: () => void }) {
  const run = useImportContacts();
  const fields = useContactFields();
  const input = useRef<HTMLInputElement>(null);
  const [preview, setPreview] = useState<ContactImportResult | null>(null);
  const [done, setDone] = useState<ContactImportResult | null>(null);
  const [tooBig, setTooBig] = useState(false);
  const [mapped, setMapped] = useState(false);

  const formik = useFormik<ImportValues>({
    initialValues: { file: null, picks: {}, onExisting: "fill_blanks" },
    validationSchema: importSchema,
    onSubmit: (v) => {
      if (!v.file) return;
      run.mutate(
        { file: v.file, dryRun: false, mapping: mappingFrom(v.picks), onExisting: v.onExisting },
        {
          onSuccess: (r) => {
            setDone(r);
            toast.success("Import finished");
          },
        },
      );
    },
  });
  const { file, picks, onExisting } = formik.values;

  const dry = (f: File, mapping: ImportMapping | undefined, existing: OnExisting) =>
    run.mutate(
      { file: f, dryRun: true, mapping, onExisting: existing },
      {
        onSuccess: (r) => {
          setPreview(r);
          // The first answer fills the dropdowns from the server's guess.
          if (!mapped) {
            setMapped(true);
            void formik.setFieldValue("picks", picksFrom(r.mapping));
          }
        },
      },
    );

  const change = (key: string, col: string) => {
    if (!file || !mapped) return;
    const next = { ...picks, [key]: col };
    void formik.setFieldValue("picks", next);
    dry(file, mappingFrom(next), onExisting);
  };

  const columns = preview?.headers ?? [];
  const options = [
    { value: "", label: "Don't import" },
    ...columns.map((c) => ({ value: c, label: c })),
  ];

  if (done) {
    return (
      <div className="space-y-4">
        <p className="text-sm font-medium text-success">Import finished.</p>
        <Numbers r={done} />
        <Problems r={done} />
        <div className="flex justify-end">
          <Button onClick={onClose}>Close</Button>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Step 1: the file. */}
      <div className="flex flex-wrap items-center gap-3">
        <input
          ref={input}
          type="file"
          accept=".csv,text/csv"
          className="sr-only"
          aria-label="CSV file"
          onChange={(e) => {
            const f = e.target.files?.[0];
            e.target.value = "";
            if (!f) return;
            setMapped(false);
            setPreview(null);
            void formik.setFieldValue("picks", {});
            if (f.size > MAX_BYTES) {
              void formik.setFieldValue("file", null);
              return setTooBig(true);
            }
            setTooBig(false);
            void formik.setFieldValue("file", f);
            dry(f, undefined, onExisting);
          }}
        />
        <Button variant="outline" onClick={() => input.current?.click()}>
          <Upload className="size-4" /> {file ? "Choose another file" : "Choose a CSV file"}
        </Button>
        {file && <span className="text-sm text-muted-foreground">{file.name}</span>}
      </div>
      {formik.submitCount > 0 && formik.errors.file && (
        <FormError message={String(formik.errors.file)} />
      )}
      {tooBig && <FormError message="That file is over 2 MB. Split it and import the parts." />}
      {run.isError && <FormError message={msg(run.error)} />}

      {/* Step 2: the columns. */}
      {preview && mapped && (
        <>
          <div className="grid gap-3 sm:grid-cols-2">
            {PROPS.map((p) => (
              <Field
                key={p.key}
                label={"required" in p ? `${p.label} (required)` : p.label}
                htmlFor={`map-${p.key}`}
              >
                <Select
                  id={`map-${p.key}`}
                  value={picks[p.key] ?? ""}
                  options={options}
                  onChange={(e) => change(p.key, e.target.value)}
                />
              </Field>
            ))}
            {fields.data?.map((f) => (
              <Field key={f.key} label={f.label} htmlFor={`map-attr-${f.key}`}>
                <Select
                  id={`map-attr-${f.key}`}
                  value={picks[`attr:${f.key}`] ?? ""}
                  options={options}
                  onChange={(e) => change(`attr:${f.key}`, e.target.value)}
                />
              </Field>
            ))}
          </div>

          <Field label="If the customer already exists" htmlFor="map-existing">
            <Select
              id="map-existing"
              value={onExisting}
              options={EXISTING}
              searchable={false}
              onChange={(e) => {
                const v = e.target.value as OnExisting;
                void formik.setFieldValue("onExisting", v);
                if (file) dry(file, mappingFrom(picks), v);
              }}
            />
          </Field>

          <div className="space-y-2 rounded-lg bg-surface-2 p-3">
            <Numbers r={preview} />
            <Problems r={preview} />
          </div>
        </>
      )}

      {/* Step 3: the real run, with the same mapping. */}
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button
          disabled={!file || !preview || !picks.email || preview.created + preview.updated === 0}
          loading={run.isPending && !run.variables?.dryRun}
          onClick={() => void formik.submitForm()}
        >
          Import
        </Button>
      </div>
    </div>
  );
}
