import { ShellState } from "../../../../../features/shell/ShellStates.tsx";

export default function ListingsLoading() {
  return (
    <div className="odp-content" data-testid="listings-route-loading">
      <ShellState kind="loading" testId="listings-state-loading" />
    </div>
  );
}
