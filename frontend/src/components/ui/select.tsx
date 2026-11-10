"use client";

import { Check, ChevronDown } from "lucide-react";
import * as React from "react";
import { type Control, type FieldPath, type FieldValues, useController } from "react-hook-form";
import ReactSelect, {
  components,
  type ClassNamesConfig,
  type DropdownIndicatorProps,
  type GroupBase,
  type OptionProps,
  type SelectInstance,
} from "react-select";

import { cn } from "@/lib/utils";

export interface SelectOption {
  value: string;
  label: string;
  disabled?: boolean;
}

export interface SelectChangeEvent {
  target: { name?: string; value: string };
}

/** Flatten `<option>` children into plain option objects. */
function optionsFromChildren(children: React.ReactNode): SelectOption[] {
  const out: SelectOption[] = [];
  const text = (n: React.ReactNode): string =>
    Array.isArray(n)
      ? n.map(text).join("")
      : typeof n === "string" || typeof n === "number"
        ? String(n)
        : "";
  const walk = (nodes: React.ReactNode) => {
    React.Children.forEach(nodes, (child) => {
      if (!React.isValidElement(child)) return;
      const p = child.props as {
        value?: string | number;
        children?: React.ReactNode;
        disabled?: boolean;
      };
      if (child.type === "option") {
        out.push({
          value: String(p.value ?? text(p.children)),
          label: text(p.children),
          disabled: p.disabled,
        });
      } else if (p.children) walk(p.children);
    });
  };
  walk(children);
  return out;
}

const baseControl = ({ isFocused, isDisabled }: { isFocused: boolean; isDisabled: boolean }) =>
  cn(
    "min-h-10 w-full cursor-pointer rounded-lg border bg-surface px-3 text-sm text-foreground transition-colors",
    isFocused ? "border-primary ring-4 ring-primary/15" : "border-input hover:border-border-strong",
    isDisabled && "cursor-not-allowed opacity-50",
  );

const classNames: ClassNamesConfig<SelectOption, false, GroupBase<SelectOption>> = {
  control: baseControl,
  valueContainer: () => "gap-1",
  singleValue: () => "truncate text-foreground",
  placeholder: () => "truncate text-subtle-foreground",
  input: () => "text-foreground",
  indicatorsContainer: () => "gap-1",
  indicatorSeparator: () => "hidden",
  dropdownIndicator: ({ selectProps }) =>
    cn("text-subtle-foreground transition-transform", selectProps.menuIsOpen && "rotate-180"),
  menu: () =>
    "z-50 my-1.5 overflow-hidden rounded-xl border border-border bg-surface p-1 shadow-pop",
  menuList: () => "max-h-60 space-y-0.5 overflow-y-auto overscroll-contain",
  option: ({ isFocused, isSelected, isDisabled }) =>
    cn(
      "block cursor-pointer rounded-md px-2.5 py-1.5 text-[13px] leading-5",
      isFocused && !isSelected && "bg-surface-2",
      isSelected ? "bg-primary-soft font-medium text-primary-ink" : "text-foreground-2",
      isDisabled && "cursor-not-allowed opacity-50",
    ),
  noOptionsMessage: () => "px-2.5 py-3 text-sm text-muted-foreground",
};

type OptProps = OptionProps<SelectOption, false>;
const DropdownIndicator = (props: DropdownIndicatorProps<SelectOption, false>) => (
  <components.DropdownIndicator {...props}>
    <ChevronDown className="size-4" aria-hidden />
  </components.DropdownIndicator>
);

const Option = (props: OptProps) => (
  <components.Option {...props}>
    <span className="flex w-full items-center justify-between gap-2">
      <span className="min-w-0 flex-1 truncate">{props.children}</span>
      {props.isSelected && <Check className="size-4 flex-none" aria-hidden />}
    </span>
  </components.Option>
);

export interface SelectProps {
  id?: string;
  name?: string;
  value?: string | null;
  onChange?: (e: SelectChangeEvent) => void;
  onBlur?: () => void;
  disabled?: boolean;
  placeholder?: string;
  /** Applied to the outer wrapper (width, margin). */
  className?: string;
  /** Applied to the visible control (height, border, background). */
  controlClassName?: string;
  children?: React.ReactNode;
  options?: SelectOption[];
  "aria-label"?: string;
  "aria-invalid"?: boolean;
  searchable?: boolean;
  /** Open the menu immediately and keep it open (used by popover pickers). */
  defaultOpen?: boolean;
}

/**
 * Searchable select built on react-select. Drop-in for the old native
 * `<Select>`: pass `<option>` children (or `options`) plus value/onChange.
 * The menu renders in a body portal so dialogs, drawers and scroll
 * containers never clip it, and it flips above when there is no room below.
 */
export const Select = React.forwardRef<SelectInstance<SelectOption, false>, SelectProps>(
  function Select(
    {
      id,
      name,
      value,
      onChange,
      onBlur,
      disabled,
      placeholder = "Select…",
      className,
      controlClassName,
      children,
      options,
      searchable = true,
      defaultOpen,
      ...aria
    },
    ref,
  ) {
    const opts = React.useMemo(() => options ?? optionsFromChildren(children), [options, children]);
    const selected = opts.find((o) => o.value === (value ?? "")) ?? null;

    return (
      <div className={cn("w-full min-w-0", className)}>
        <ReactSelect<SelectOption, false>
          ref={ref}
          unstyled
          instanceId={id}
          inputId={id}
          name={name}
          aria-label={aria["aria-label"]}
          aria-invalid={aria["aria-invalid"]}
          options={opts}
          value={selected}
          isDisabled={disabled}
          isSearchable={searchable}
          isOptionDisabled={(o) => !!o.disabled}
          placeholder={placeholder}
          menuPortalTarget={typeof document === "undefined" ? undefined : document.body}
          menuPosition="fixed"
          menuPlacement="auto"
          menuShouldScrollIntoView={false}
          defaultMenuIsOpen={defaultOpen}
          // Opened on purpose by the user (inline editor), so moving focus is expected.
          // eslint-disable-next-line jsx-a11y/no-autofocus
          autoFocus={defaultOpen}
          blurInputOnSelect
          onBlur={onBlur}
          onChange={(o) => o && onChange?.({ target: { name, value: o.value } })}
          noOptionsMessage={() => "No matches"}
          components={{ DropdownIndicator, Option }}
          classNames={{
            ...classNames,
            control: (state) =>
              cn(baseControl(state), aria["aria-invalid"] && "border-danger!", controlClassName),
          }}
          styles={{ menuPortal: (base) => ({ ...base, zIndex: 100 }) }}
        />
      </div>
    );
  },
);

/** react-hook-form binding: `<FormSelect control={control} name="priority" id="priority">…</FormSelect>`. */
export function FormSelect<T extends FieldValues>({
  control,
  name,
  ...props
}: Omit<SelectProps, "value" | "onChange" | "onBlur" | "name"> & {
  control: Control<T>;
  name: FieldPath<T>;
}) {
  const { field } = useController({ control, name });
  return (
    <Select
      {...props}
      name={field.name}
      value={(field.value as string | undefined) ?? ""}
      onChange={(e) => field.onChange(e.target.value)}
      onBlur={field.onBlur}
    />
  );
}
