// The framer-motion feature bundle the app runs on: `domMax` (animations,
// gestures, drag and layout). Only ever reached through the dynamic import
// in ./motionFeatures, so webpack puts it in its own async chunk and no
// route's first load carries it. Do not import this file statically.

import { domMax } from "framer-motion";

export default domMax;
