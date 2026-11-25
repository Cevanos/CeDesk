import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * Shared table building blocks so every table has the same header, padding, dividers and
 * horizontal-scroll behaviour. `minWidth` keeps columns readable on a phone: the table scrolls
 * sideways inside its card instead of squashing.
 */
export function DataTable({
  minWidth = "40rem",
  className,
  wrapperClassName,
  children,
  ...rest
}: React.TableHTMLAttributes<HTMLTableElement> & {
  minWidth?: string;
  wrapperClassName?: string;
}) {
  return (
    <div className={cn("overflow-x-auto", wrapperClassName)}>
      <table style={{ minWidth }} className={cn("w-full text-left text-sm", className)} {...rest}>
        {children}
      </table>
    </div>
  );
}

export function THead({ className, ...rest }: React.HTMLAttributes<HTMLTableSectionElement>) {
  return <thead className={cn("border-b border-border bg-surface-2/60", className)} {...rest} />;
}

export function TBody({ className, ...rest }: React.HTMLAttributes<HTMLTableSectionElement>) {
  return <tbody className={cn("divide-y divide-border", className)} {...rest} />;
}

export function Tr({
  interactive,
  className,
  ...rest
}: React.HTMLAttributes<HTMLTableRowElement> & { interactive?: boolean }) {
  return (
    <tr
      className={cn(
        "align-middle",
        interactive &&
          "cursor-pointer hover:bg-surface-2 focus-visible:bg-surface-2 focus-visible:outline-none",
        !interactive && "hover:bg-surface-2/50",
        className,
      )}
      {...rest}
    />
  );
}

export function Th({
  right,
  className,
  ...rest
}: React.ThHTMLAttributes<HTMLTableCellElement> & { right?: boolean }) {
  return (
    <th
      scope="col"
      className={cn(
        "px-4 py-2.5 text-xs font-semibold whitespace-nowrap text-muted-foreground",
        right ? "text-right" : "text-left",
        className,
      )}
      {...rest}
    />
  );
}

/** Row header cell: the first, identifying cell of a row. */
export function RowTh({ className, ...rest }: React.ThHTMLAttributes<HTMLTableCellElement>) {
  return <th scope="row" className={cn("px-4 py-3 text-left font-medium", className)} {...rest} />;
}

export function Td({
  right,
  muted,
  className,
  ...rest
}: React.TdHTMLAttributes<HTMLTableCellElement> & { right?: boolean; muted?: boolean }) {
  return (
    <td
      className={cn(
        "px-4 py-3",
        right && "text-right tabular-nums",
        muted && "text-muted-foreground",
        className,
      )}
      {...rest}
    />
  );
}
