"use client";

import { RotateCcw, ShieldAlert, Tag } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Badge, Button, Card, Skeleton } from "@/components/ui/primitives";
import { Switch } from "@/components/ui/switch";
import { TagInput } from "@/components/ui/tag-input";
import { FormError, fieldErrors, LoadError } from "@/components/workspace/list-shared";
import { useUnsavedChanges } from "@/hooks/use-unsaved-changes";
import { useSaveTriageSettings, useTriageSettings } from "@/hooks/use-workspace";
import type { TriageSettings } from "@/lib/api/types";

export function topicLabel(key: string): string {
  const s = key.replace(/[_-]+/g, " ").trim();
  return s ? s[0]!.toUpperCase() + s.slice(1) : key;
}

export function TopicsView() {
  const q = useTriageSettings();
  if (q.isPending) return <Skeleton className="h-64" />;
  if (q.isError) return <LoadError error={q.error} />;
  // Re-mount when the saved data changes (save, reset), so the draft starts from it.
  return <TopicsBody key={JSON.stringify(q.data)} saved={q.data} />;
}

function TopicsBody({ saved }: { saved: TriageSettings }) {
  const save = useSaveTriageSettings();
  const [topics, setTopics] = useState(saved.topics);
  const [redact, setRedact] = useState(saved.redact_cards);
  const [errs, setErrs] = useState<Record<string, string>>({});

  const dirty =
    redact !== saved.redact_cards || JSON.stringify(topics) !== JSON.stringify(saved.topics);
  useUnsavedChanges(dirty);

  const names = Object.keys(topics);

  return (
    <div className="space-y-6">
      <section aria-labelledby="topics-h">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 id="topics-h" className="text-base font-semibold">
              Topics
            </h2>
            <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
              A ticket gets a topic when its subject or first message contains one of the keywords.
              Agents see the topic as a chip on the ticket and can filter by it.
              {saved.is_default && " (Using the built-in lists.)"}
            </p>
          </div>
          <Button
            variant="outline"
            size="sm"
            disabled={save.isPending}
            onClick={() => {
              if (
                confirm("Reset every topic to the built-in keyword lists? Your changes are lost.")
              )
                save.mutate(
                  { reset: true, redact_cards: saved.redact_cards },
                  { onSuccess: () => toast.success("Topics reset to the built-in lists") },
                );
            }}
          >
            <RotateCcw className="size-3.5" /> Reset to built-in lists
          </Button>
        </div>

        {names.length === 0 ? (
          <Card className="mt-4 p-6 text-center text-sm text-muted-foreground">
            No topics are set up yet.
          </Card>
        ) : (
          <div className="mt-4 grid gap-3 lg:grid-cols-2">
            {names.map((name) => (
              <Card key={name} className="p-4">
                <div className="mb-2.5 flex items-center gap-2">
                  <span className="grid size-7 place-items-center rounded-lg bg-primary-soft text-primary-ink">
                    <Tag className="size-3.5" aria-hidden />
                  </span>
                  <h3 className="text-sm font-semibold">{topicLabel(name)}</h3>
                  <Badge className="ml-auto">{topics[name]?.length ?? 0} keywords</Badge>
                </div>
                <TagInput
                  label={`${topicLabel(name)} keywords`}
                  tags={topics[name] ?? []}
                  placeholder="Type a keyword and press Enter"
                  onChange={(tags) => setTopics((t) => ({ ...t, [name]: tags }))}
                />
                {errs[`topics.${name}`] && (
                  <p role="alert" className="mt-1 text-xs text-danger">
                    {errs[`topics.${name}`]}
                  </p>
                )}
              </Card>
            ))}
          </div>
        )}
      </section>

      <section aria-labelledby="redact-h">
        <h2 id="redact-h" className="text-base font-semibold">
          Card redaction
        </h2>
        <Card className="mt-3 p-4 sm:p-5">
          <div className="flex items-start gap-4">
            <div className="min-w-0 flex-1">
              <p id="redact-label" className="text-sm font-medium">
                Hide card numbers in new messages
              </p>
              <p id="redact-desc" className="mt-1 text-sm text-muted-foreground">
                When a customer message contains a payment card number, it is masked before the
                message is stored (for example ●●●● ●●●● ●●●● 4242).
              </p>
            </div>
            <Switch
              checked={redact}
              onChange={setRedact}
              labelledBy="redact-label"
              describedBy="redact-desc"
            />
          </div>
          <div
            role="note"
            className="mt-4 flex gap-2 rounded-lg border border-warning/30 bg-warning-soft px-3 py-2.5 text-[13px] text-warning"
          >
            <ShieldAlert className="mt-0.5 size-4 flex-none" aria-hidden />
            <p>
              The original number is not kept, so it can&apos;t be recovered. Messages that are
              already stored are not changed, and agents can no longer read a card number a customer
              sends.
            </p>
          </div>
        </Card>
      </section>

      <FormError message={errs._form} />
      <div className="sticky bottom-3 flex items-center justify-end gap-3 rounded-xl border border-border bg-surface/90 p-3 shadow-pop backdrop-blur">
        {dirty && <span className="mr-auto text-sm text-muted-foreground">Unsaved changes</span>}
        <Button
          variant="ghost"
          disabled={!dirty || save.isPending}
          onClick={() => {
            setTopics(saved.topics);
            setRedact(saved.redact_cards);
            setErrs({});
          }}
        >
          Discard
        </Button>
        <Button
          disabled={!dirty}
          loading={save.isPending}
          onClick={() =>
            save.mutate(
              { topics, redact_cards: redact, shape: saved.shape },
              {
                onSuccess: () => toast.success("Topics saved"),
                onError: (e) => setErrs(fieldErrors(e)),
              },
            )
          }
        >
          Save changes
        </Button>
      </div>
    </div>
  );
}
