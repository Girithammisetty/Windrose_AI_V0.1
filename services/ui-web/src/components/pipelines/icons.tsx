"use client";
import { Database, Wand2, FlaskConical, Wrench, Boxes } from "lucide-react";

/** Category → icon for the step palette (display only). */
export function StepCategoryIcon({ category, className }: { category: string; className?: string }) {
  const Icon =
    category === "io"
      ? Database
      : category === "data_prep"
        ? Wand2
        : category === "algorithm"
          ? FlaskConical
          : category === "utility"
            ? Wrench
            : Boxes;
  return <Icon className={className} aria-hidden />;
}
