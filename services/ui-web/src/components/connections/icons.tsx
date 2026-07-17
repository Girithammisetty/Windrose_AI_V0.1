"use client";
import { Database, Warehouse, HardDrive, FolderTree, Cloud, Plug, UploadCloud } from "lucide-react";

/** Category → icon for the connector picker (display only). */
export function CategoryIcon({ category, className }: { category: string; className?: string }) {
  const Icon =
    category === "file-upload"
      ? UploadCloud
      : category === "database"
        ? Database
        : category === "warehouse"
          ? Warehouse
          : category === "object-store"
            ? HardDrive
            : category === "file"
              ? FolderTree
              : category === "saas"
                ? Cloud
                : Plug;
  return <Icon className={className} aria-hidden />;
}
