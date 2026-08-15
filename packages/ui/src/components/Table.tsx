import type { ReactNode } from "react";
import { EmptyState } from "./EmptyState.tsx";
import type {
  AsyncContract,
  Density,
  TableColumnSpec,
  TablePagination,
  TableSelection,
  TableSort,
} from "./contracts.ts";

export type TableProps<TData, TId extends string | number = string> = AsyncContract & {
  columns: readonly TableColumnSpec<TData>[];
  data: readonly TData[];
  density?: Density;
  sort?: TableSort;
  onSortChange?: (sort: TableSort) => void;
  pagination?: TablePagination;
  selection?: TableSelection<TId>;
  onRowOpen?: (row: TData, rowIndex: number) => void;
  maskedFields?: readonly string[];
  getRowKey?: (row: TData, rowIndex: number) => string;
  summary?: string;
  className?: string;
};

function cellValue<TData>(row: TData, column: TableColumnSpec<TData>): ReactNode {
  if (column.render) {
    return column.render(row);
  }
  if (column.accessor) {
    return String(row[column.accessor] ?? "");
  }
  return null;
}

export function Table<TData, TId extends string | number = string>({
  columns,
  data,
  density = "comfortable",
  sort,
  onSortChange,
  pagination,
  selection,
  onRowOpen,
  maskedFields = [],
  getRowKey,
  summary,
  loading,
  error,
  emptyState,
  className,
}: TableProps<TData, TId>) {
  const columnCount = columns.length + (selection ? 1 : 0);

  const toggleRow = (rowIndex: number) => {
    if (!selection) {
      return;
    }
    const rowId = selection.getRowId(rowIndex);
    const selected = selection.selectedIds.includes(rowId);
    selection.onChange(
      selected
        ? selection.selectedIds.filter((id) => id !== rowId)
        : [...selection.selectedIds, rowId],
    );
  };

  return (
    <div className={["odp-table-wrap", className].filter(Boolean).join(" ")} data-density={density}>
      {error ? (
        <div className="odp-inline-error" role="alert">
          {error.message}（correlation: {error.correlation_id}）
        </div>
      ) : null}
      <table className="odp-table">
        {summary ? <caption>{summary}</caption> : null}
        <thead>
          <tr>
            {selection ? <th scope="col">選取</th> : null}
            {columns.map((column) => {
              const sorted = sort?.columnId === column.id ? sort.direction : undefined;
              return (
                <th key={column.id} scope="col" aria-sort={sorted === "asc" ? "ascending" : sorted === "desc" ? "descending" : "none"} data-align={column.align}>
                  {column.sortable && onSortChange ? (
                    <button
                      className="odp-table__sort"
                      type="button"
                      onClick={() =>
                        onSortChange({
                          columnId: column.id,
                          direction: sorted === "asc" ? "desc" : "asc",
                        })
                      }
                    >
                      {column.header}
                    </button>
                  ) : (
                    column.header
                  )}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {loading ? (
            <tr>
              <td colSpan={columnCount}>載入中</td>
            </tr>
          ) : data.length === 0 ? (
            <tr>
              <td colSpan={columnCount}>
                {emptyState ? <EmptyState {...emptyState} /> : "目前沒有符合條件的資料"}
              </td>
            </tr>
          ) : (
            data.map((row, rowIndex) => (
              <tr
                key={getRowKey ? getRowKey(row, rowIndex) : String(rowIndex)}
                tabIndex={onRowOpen ? 0 : undefined}
                onClick={() => onRowOpen?.(row, rowIndex)}
                onKeyDown={(event) => {
                  if (onRowOpen && (event.key === "Enter" || event.key === " ")) {
                    event.preventDefault();
                    onRowOpen(row, rowIndex);
                  }
                }}
              >
                {selection ? (
                  <td>
                    <input
                      type="checkbox"
                      aria-label={`選取第 ${rowIndex + 1} 列`}
                      checked={selection.selectedIds.includes(selection.getRowId(rowIndex))}
                      onChange={() => toggleRow(rowIndex)}
                      onClick={(event) => event.stopPropagation()}
                    />
                  </td>
                ) : null}
                {columns.map((column) => {
                  const masked = column.masked || maskedFields.includes(column.id);
                  return (
                    <td key={column.id} data-align={column.align} data-masked={masked || undefined}>
                      {masked ? "已遮罩" : cellValue(row, column)}
                    </td>
                  );
                })}
              </tr>
            ))
          )}
        </tbody>
      </table>
      {pagination ? (
        <footer className="odp-table__pagination">
          <span>
            第 {pagination.page} 頁，每頁 {pagination.pageSize} 筆，共 {pagination.total} 筆
          </span>
          <button
            type="button"
            className="odp-text-button"
            disabled={pagination.page <= 1}
            onClick={() => pagination.onPageChange?.(pagination.page - 1)}
          >
            上一頁
          </button>
          <button
            type="button"
            className="odp-text-button"
            disabled={pagination.page * pagination.pageSize >= pagination.total}
            onClick={() => pagination.onPageChange?.(pagination.page + 1)}
          >
            下一頁
          </button>
        </footer>
      ) : null}
    </div>
  );
}
