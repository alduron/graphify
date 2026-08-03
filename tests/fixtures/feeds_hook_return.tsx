// A prop bound to a hook's return value (destructured), the motivating case
// from Plan 59 Phase 5: "what feeds sessionOptions" should not require
// grepping the prop name across the repo.
import { useSessionOptions } from "./useSessionOptions";

export function SessionPanel() {
    const { sessionOptions } = useSessionOptions();
    return <SessionForm options={sessionOptions} />;
}
