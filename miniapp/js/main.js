// Entry point — imported modules wire up their own event listeners as a
// side effect of being evaluated; this file just needs to pull them all in
// (dependency order is handled by the import graph) and kick off bootstrap.
//
// ripple.js/card-title.js/tabs.js/connect.js/profile.js are never imported
// by onboarding.js or mainApp.js, so without pulling them in here their
// self-registering listeners (ripple click-feedback, [data-title] header
// injection, tab bar switching, phone/code/password step forms, and the
// profile role-change modal) would simply never run.
import "./telegram.js";
import "./ripple.js";
import "./card-title.js";
import "./tabs.js";
import "./connect.js";
import "./profile.js";
import { bootstrap } from "./onboarding.js";

bootstrap();
