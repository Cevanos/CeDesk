"use client";

import { Pencil, Plus, Trash2, Wand2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { useConfirm } from "@/components/ui/confirm";
import { Dialog } from "@/components/ui/overlay";
import { Button, Card, EmptyState, Field, Input, Skeleton } from "@/components/ui/primitives";
import {
  ItemBuilder,
  MACRO_STEP_KINDS,
  summarize,
  type Item,
} from "@/components/workspace/item-builder";
import {
  FormError,
  fieldErrors,
  LoadError,
  movedIds,
  Problems,
  ReorderButtons,
} from "@/components/workspace/list-shared";
import { useDeleteMacro, useMacros, useReorderMacros, useSaveMacro } from "@/hooks/use-workspace";
import type { Macro } from "@/lib/api/types";

export function MacrosView() {
  const macros = useMacros();
  const del = useDeleteMacro();
  const reorder = useReorderMacros();
  const { ask, dialog } = useConfirm();
  const [editing, setEditing] = useState<Macro | "new" | null>(null);

  const move = (i: number, dir: -1 | 1) => {
    const ids = movedIds(
      (macros.data ?? []).map((m) => m.id),
      i,
      dir,
    );
    if (ids) reorder.mutate(ids);
  };

  return (
    <div>
      {dialog}
      <div className="mb-4 flex items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">
          A macro runs several steps on a ticket with one click. If one step is refused, nothing is
          saved.
        </p>
        <Button onClick={() => setEditing("new")}>
          <Plus className="size-4" /> Add macro
        </Button>
      </div>

      {macros.isPending ? (
        <Skeleton className="h-32" />
      ) : macros.isError ? (
        <LoadError error={macros.error} />
      ) : macros.data.length === 0 ? (
        <Card>
          <EmptyState icon={<Wand2 className="size-6" />} title="No macros yet">
            Add one for a routine like “Refund approved”: reply, tag and resolve in one click.
          </EmptyState>
        </Card>
      ) : (
        <Card className="overflow-hidden">
          <ul className="divide-y divide-border">
            {macros.data.map((m, i) => (
              <li key={m.id} className="px-4 py-3">
                <div className="flex flex-wrap items-center gap-3">
                  <ReorderButtons
                    name={m.name}
                    index={i}
                    count={macros.data.length}
                    disabled={reorder.isPending}
                    onMove={(d) => move(i, d)}
                  />
                  <div className="min-w-0 flex-1 basis-48">
                    <div className="font-medium">{m.name}</div>
                    <div className="truncate text-xs text-muted-foreground">
                      {m.steps.map((s) => summarize(s as Item, MACRO_STEP_KINDS)).join(" → ")}
                    </div>
                  </div>
                  <Button size="sm" variant="outline" onClick={() => setEditing(m)}>
                    <Pencil className="size-3.5" /> Edit
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={del.isPending}
                    onClick={() =>
                      ask({
                        title: "Delete this macro?",
                        message: "This can't be undone.",
                        detail: m.name,
                        confirmLabel: "Delete",
                        run: () =>
                          del.mutate(m.id, { onSuccess: () => toast.success("Macro deleted") }),
                      })
                    }
                  >
                    <Trash2 className="size-3.5" /> Delete
                  </Button>
                </div>
                <Problems items={m.problems} />
              </li>
            ))}
          </ul>
        </Card>
      )}

      <Dialog
        open={editing !== null}
        onClose={() => setEditing(null)}
        title={editing === "new" ? "Add macro" : "Edit macro"}
        className="max-w-2xl"
      >
        {editing !== null && (
          <MacroForm
            key={editing === "new" ? "new" : editing.id}
            macro={editing === "new" ? null : editing}
            onClose={() => setEditing(null)}
          />
        )}
      </Dialog>
    </div>
  );
}

function MacroForm({ macro, onClose }: { macro: Macro | null; onClose: () => void }) {
  const save = useSaveMacro();
  const [name, setName] = useState(macro?.name ?? "");
  const [steps, setSteps] = useState<Item[]>((macro?.steps as Item[] | undefined) ?? []);
  const [errs, setErrs] = useState<Record<string, string>>({});

  return (
    <form
      noValidate
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        const next: Record<string, string> = {};
        if (!name.trim()) next.name = "Enter a name";
        if (steps.length === 0) next.steps = "Add at least one step";
        setErrs(next);
        if (Object.keys(next).length) return;
        save.mutate(
          { id: macro?.id, name: name.trim(), steps },
          {
            onSuccess: () => {
              toast.success("Macro saved");
              onClose();
            },
            onError: (err) => setErrs(fieldErrors(err)),
          },
        );
      }}
    >
      <Field label="Name" htmlFor="m-name" error={errs.name}>
        <Input id="m-name" value={name} maxLength={80} onChange={(e) => setName(e.target.value)} />
      </Field>
      <ItemBuilder
        title="Steps"
        addLabel="Add step"
        kinds={MACRO_STEP_KINDS}
        items={steps}
        onChange={setSteps}
      />
      {(errs.steps || errs.macro) && (
        <p role="alert" className="text-xs text-danger">
          {errs.steps ?? errs.macro}
        </p>
      )}
      <FormError message={errs._form} />
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button type="submit" loading={save.isPending}>
          Save macro
        </Button>
      </div>
    </form>
  );
}
