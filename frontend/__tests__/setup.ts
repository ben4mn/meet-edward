// Registered via `node --import ./__tests__/setup.ts`.
// Brings up a jsdom window so React rendering works under node:test.
// Pure-logic tests don't touch the DOM and ignore this; the cost is a
// one-time global install that's already required for component tests.

import "global-jsdom/register";

// The owner every test speaks about, as the backend's conftest sets
// EDWARD_OWNER_NAME=Ellis. In the app the name arrives with
// /api/auth/status (lib/owner.ts); model-written text names the owner
// ("Remind Ellis to …"), so the matching code needs one. A test about the
// unnamed case (or another name) sets its own in a beforeEach; node --test
// runs each file in its own process, so nothing leaks between files.
import { setOwnerName } from "../lib/owner.ts";

setOwnerName("Ellis");
