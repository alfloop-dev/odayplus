import { ShellState } from "../../../../../../../features/shell/ShellStates.tsx";

export default function IntakeDetailLoading() {
  return (
    <div className="odp-content" data-testid="intake-route-loading">
      <ShellState kind="loading" testId="intake-route-state-loading" />
    </div>
  );
}
