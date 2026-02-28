"use client";

import { useState, useRef, useCallback, useEffect, KeyboardEvent, DragEvent, ClipboardEvent } from "react";
import { Send, Loader2, Square, Paperclip, X, FileText, Image as ImageIcon } from "lucide-react";

interface InputBarProps {
  onSend: (message: string, files?: File[]) => void;
  onStop?: () => void;
  isLoading: boolean;
  canStop?: boolean;
}

const ACCEPTED_TYPES = "image/png,image/jpeg,image/gif,image/webp,application/pdf,text/plain,text/csv,application/json";
const MAX_FILE_SIZE = 10 * 1024 * 1024; // 10 MB

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function InputBar({
  onSend,
  onStop,
  isLoading,
  canStop = false,
}: InputBarProps) {
  const [message, setMessage] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const addFiles = useCallback((newFiles: FileList | File[]) => {
    const validFiles = Array.from(newFiles).filter(
      (f) => f.size <= MAX_FILE_SIZE
    );
    setFiles((prev) => [...prev, ...validFiles]);
  }, []);

  const removeFile = useCallback((index: number) => {
    setFiles((prev) => prev.filter((_, i) => i !== index));
  }, []);

  const handleSubmit = () => {
    if ((message.trim() || files.length > 0) && !isLoading) {
      onSend(message, files.length > 0 ? files : undefined);
      setMessage("");
      setFiles([]);
    }
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  const handleFileSelect = () => {
    fileInputRef.current?.click();
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) {
      addFiles(e.target.files);
      // Reset input so the same file can be selected again
      e.target.value = "";
    }
  };

  const handlePaste = (e: ClipboardEvent<HTMLTextAreaElement>) => {
    const items = e.clipboardData?.items;
    if (!items) return;

    const imageFiles: File[] = [];
    for (const item of Array.from(items)) {
      if (item.type.startsWith("image/")) {
        const file = item.getAsFile();
        if (file) {
          imageFiles.push(file);
        }
      }
    }

    if (imageFiles.length > 0) {
      addFiles(imageFiles);
    }
  };

  const handleDragOver = (e: DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(true);
  };

  const handleDragLeave = (e: DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
  };

  const handleDrop = (e: DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);

    if (e.dataTransfer?.files) {
      addFiles(e.dataTransfer.files);
    }
  };

  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(textarea.scrollHeight, 192)}px`;
  }, [message]);

  return (
    <div className="bg-primary-bg p-3 sm:p-4 flex-shrink-0">
      <div className="container mx-auto max-w-3xl">
        {/* File preview strip */}
        {files.length > 0 && (
          <div className="flex flex-wrap gap-2 mb-2 px-1">
            {files.map((file, index) => (
              <div
                key={`${file.name}-${index}`}
                className="flex items-center gap-1.5 bg-surface border border-border rounded-lg px-2.5 py-1.5 text-sm"
              >
                {/* eslint-disable @next/next/no-img-element */}
                {file.type.startsWith("image/") ? (
                  <img
                    src={URL.createObjectURL(file)}
                    alt={file.name}
                    className="w-8 h-8 rounded object-cover flex-shrink-0"
                    onLoad={(e) => {
                      // Revoke object URL after image loads to free memory
                      URL.revokeObjectURL((e.target as HTMLImageElement).src);
                    }}
                  />
                ) : (
                  <FileText className="w-4 h-4 text-text-muted flex-shrink-0" />
                )}
                {/* eslint-enable @next/next/no-img-element */}
                <span className="text-text-secondary truncate max-w-[120px]">{file.name}</span>
                <span className="text-text-muted text-xs">{formatFileSize(file.size)}</span>
                <button
                  onClick={() => removeFile(index)}
                  className="text-text-muted hover:text-text-primary ml-0.5 flex-shrink-0"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
            ))}
          </div>
        )}

        <div
          className={`flex items-end gap-2 rounded-2xl border ${
            isDragging ? "border-terminal bg-terminal/5" : "border-input-border bg-input-bg"
          } p-2 focus-within:ring-2 focus-within:ring-terminal focus-within:border-transparent transition-all`}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
        >
          {/* File attach button */}
          <button
            onClick={handleFileSelect}
            disabled={isLoading}
            className="w-9 h-9 rounded-full hover:bg-surface flex items-center justify-center transition-colors flex-shrink-0 touch-manipulation text-text-muted hover:text-text-secondary disabled:opacity-30"
            title="Attach files"
          >
            <Paperclip className="w-4 h-4" />
          </button>

          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept={ACCEPTED_TYPES}
            onChange={handleFileChange}
            className="hidden"
          />

          <textarea
            ref={textareaRef}
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            onKeyDown={handleKeyDown}
            onPaste={handlePaste}
            placeholder={isDragging ? "Drop files here..." : "Message Edward..."}
            className="flex-1 resize-none bg-transparent px-2 py-1.5 text-base text-text-primary placeholder:text-text-muted focus:outline-none min-h-[28px] max-h-[192px] overflow-y-auto"
            disabled={isLoading}
          />

          {/* Send/Stop button */}
          {canStop ? (
            <button
              onClick={onStop}
              className="w-9 h-9 rounded-full bg-red-500 text-white hover:bg-red-600 flex items-center justify-center transition-colors flex-shrink-0 touch-manipulation"
              title="Stop generating"
            >
              <Square className="w-4 h-4 fill-current" />
            </button>
          ) : (
            <button
              onClick={handleSubmit}
              disabled={(!message.trim() && files.length === 0) || isLoading}
              className="w-9 h-9 rounded-full bg-terminal text-white hover:opacity-80 disabled:opacity-30 disabled:cursor-not-allowed flex items-center justify-center transition-opacity flex-shrink-0 touch-manipulation"
            >
              {isLoading ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Send className="w-4 h-4" />
              )}
            </button>
          )}
        </div>
        <p className="hidden sm:block text-xs text-text-muted mt-2 text-center">
          Press Enter to send, Shift+Enter for new line
        </p>
      </div>
    </div>
  );
}
