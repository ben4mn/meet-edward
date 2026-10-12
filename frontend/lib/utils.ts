// One cn for the whole app: the tailwind-merge in components/ui/cn.ts knows
// the type scale (text-body …), the elevation shadows and the motion
// durations. A stock twMerge would read `text-body` as a colour and drop it.
export { cn } from "@/components/ui/cn";
