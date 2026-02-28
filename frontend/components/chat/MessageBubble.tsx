"use client";

import { useState, useCallback } from "react";
import { cn } from "@/lib/utils";
import { User, AlertCircle, FileText, Download, X, Copy, Check, Activity, Clock } from "lucide-react";
import { EdwardAvatar } from "@/components/EdwardAvatar";
import { MarkdownContent } from "./MarkdownContent";
import { CodeBlock } from "./CodeBlock";
import { PlanBlock } from "./PlanBlock";
import { CCSessionBlock } from "./CCSessionBlock";
import { ThinkingIndicator } from "./ThinkingIndicator";
import type { Message, MessageAttachment } from "@/lib/ChatContext";

interface MessageBubbleProps {
  message: Message;
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function AttachmentImage({ attachment }: { attachment: MessageAttachment }) {
  const [fullscreen, setFullscreen] = useState(false);
  const src = attachment.preview_url || (attachment.file_id ? `/api/files/${attachment.file_id}/download` : undefined);

  if (!src) return null;

  return (
    <>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={src}
        alt={attachment.filename}
        className="max-w-[200px] max-h-[200px] rounded-md cursor-pointer hover:opacity-90 transition-opacity object-cover"
        onClick={() => setFullscreen(true)}
      />
      {fullscreen && (
        <div
          className="fixed inset-0 z-50 bg-black/80 flex items-center justify-center p-4 cursor-pointer"
          onClick={() => setFullscreen(false)}
        >
          <button
            onClick={() => setFullscreen(false)}
            className="absolute top-4 right-4 text-white/80 hover:text-white"
          >
            <X className="w-6 h-6" />
          </button>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={src}
            alt={attachment.filename}
            className="max-w-[90vw] max-h-[90vh] object-contain rounded-lg"
            onClick={(e) => e.stopPropagation()}
          />
        </div>
      )}
    </>
  );
}

function AttachmentBadge({ attachment }: { attachment: MessageAttachment }) {
  const downloadUrl = attachment.file_id ? `/api/files/${attachment.file_id}/download` : undefined;

  return (
    <div className="inline-flex items-center gap-1.5 bg-surface-elevated/50 border border-border rounded-md px-2 py-1 text-xs">
      <FileText className="w-3.5 h-3.5 text-text-muted flex-shrink-0" />
      <span className="text-text-secondary truncate max-w-[150px]">{attachment.filename}</span>
      <span className="text-text-muted">{formatFileSize(attachment.size)}</span>
      {downloadUrl && (
        <a
          href={downloadUrl}
          download={attachment.filename}
          className="text-terminal hover:text-terminal/80 ml-0.5"
          onClick={(e) => e.stopPropagation()}
        >
          <Download className="w-3 h-3" />
        </a>
      )}
    </div>
  );
}

function Attachments({ attachments }: { attachments: MessageAttachment[] }) {
  const images = attachments.filter((a) => a.mime_type.startsWith("image/"));
  const files = attachments.filter((a) => !a.mime_type.startsWith("image/"));

  return (
    <div className="mb-2">
      {images.length > 0 && (
        <div className="flex flex-wrap gap-2 mb-1.5">
          {images.map((att) => (
            <AttachmentImage key={att.id} attachment={att} />
          ))}
        </div>
      )}
      {files.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {files.map((att) => (
            <AttachmentBadge key={att.id} attachment={att} />
          ))}
        </div>
      )}
    </div>
  );
}

function parseTriggerContent(content: string): { label: string; fields: [string, string][] } {
  const isHeartbeat = content.trim().startsWith("[HEARTBEAT EVENT]");
  const label = isHeartbeat ? "Heartbeat Trigger" : "Scheduled Event Trigger";

  // Strip the prefix tag
  let body = content
    .replace(/^\[HEARTBEAT EVENT\]\s*/i, "")
    .replace(/^\[SCHEDULED EVENT\]\s*/i, "")
    .trim();

  // Parse key: value lines
  const fields: [string, string][] = [];
  const lines = body.split("\n");
  for (const line of lines) {
    const match = line.match(/^([A-Za-z][A-Za-z ]+?):\s*(.+)$/);
    if (match) {
      const key = match[1].trim();
      const val = match[2].trim();
      // Skip internal metadata
      if (key === "Conversation ID") continue;
      fields.push([key, val]);
    }
  }

  // If no structured fields found, just show the body as description
  if (fields.length === 0 && body) {
    fields.push(["Description", body]);
  }

  return { label, fields };
}

function TriggerCard({ message }: { message: Message }) {
  const { label, fields } = parseTriggerContent(message.content);
  const Icon = message.triggerType === "heartbeat" ? Activity : Clock;

  return (
    <div className="flex gap-3">
      <EdwardAvatar size="sm" className="flex-shrink-0" />
      <div className="max-w-[80%]">
        <div className="rounded-lg border border-border bg-surface-elevated/50 px-4 py-3">
          <div className="flex items-center gap-2 mb-2 text-text-muted text-xs font-medium uppercase tracking-wide">
            <Icon className="w-3.5 h-3.5" />
            <span>{label}</span>
          </div>
          <div className="space-y-1">
            {fields.map(([key, val], i) => (
              <div key={i} className="flex gap-2 text-sm">
                <span className="text-text-muted flex-shrink-0">{key}:</span>
                <span className="text-text-secondary">{val}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

export function MessageBubble({ message }: MessageBubbleProps) {
  const isUser = message.role === "user";
  const hasCodeBlocks = message.codeBlocks && message.codeBlocks.length > 0;
  const hasAttachments = message.attachments && message.attachments.length > 0;
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(async () => {
    if (!message.content) return;
    await navigator.clipboard.writeText(message.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }, [message.content]);

  // Render trigger messages as a compact info card
  if (message.isTrigger) {
    return <TriggerCard message={message} />;
  }

  const showCopyButton = !isUser && message.content && !message.isThinking;

  return (
    <div className={cn("flex gap-3", isUser && "flex-row-reverse")}>
      {isUser ? (
        <div className="w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0 bg-surface text-text-primary">
          <User className="w-4 h-4" />
        </div>
      ) : (
        <EdwardAvatar size="sm" className="flex-shrink-0" />
      )}
      <div className="group max-w-[80%]">
        <div
          className={cn(
            "rounded-lg px-4 py-2",
            isUser
              ? "bg-terminal text-white"
              : "bg-surface text-text-primary border border-border"
          )}
        >
          {/* Attachments */}
          {hasAttachments && (
            <Attachments attachments={message.attachments!} />
          )}

          {/* Progress/Thinking indicator (unified: auto-transitions from active to completed) */}
          {message.progressSteps && message.progressSteps.length > 0 && (
            <ThinkingIndicator
              progressSteps={message.progressSteps}
              isStreaming={!!message.isThinking || !message.content}
            />
          )}

          {/* Plan block */}
          {message.planBlock && (
            <PlanBlock block={message.planBlock} />
          )}

          {/* CC session blocks */}
          {message.ccSessions && message.ccSessions.length > 0 && (
            <div className="mb-2">
              {message.ccSessions.map((session) => (
                <CCSessionBlock key={session.id} session={session} />
              ))}
            </div>
          )}

          {/* Code blocks */}
          {hasCodeBlocks && (
            <div className="mb-2">
              {message.codeBlocks!.map((block) => (
                <CodeBlock key={block.id} block={block} />
              ))}
            </div>
          )}

          {/* Text content */}
          {message.content && (
            isUser ? (
              <p className="whitespace-pre-wrap">{message.content}</p>
            ) : (
              <MarkdownContent content={message.content} />
            )
          )}

          {/* Empty state for assistant messages that are still loading */}
          {!isUser && !message.content && !hasCodeBlocks && !message.planBlock && !message.isThinking && !message.wasInterrupted && (
            <ThinkingIndicator content="Thinking..." />
          )}

          {/* Interrupted indicator */}
          {message.wasInterrupted && (
            <div className="flex items-center gap-2 text-text-muted text-sm mt-2 pt-2 border-t border-border">
              <AlertCircle className="w-3.5 h-3.5" />
              <span>Response stopped</span>
            </div>
          )}
        </div>
        {showCopyButton && (
          <button
            onClick={handleCopy}
            className="mt-1 p-1 rounded text-text-muted hover:text-text-primary opacity-0 group-hover:opacity-100 transition-opacity"
            aria-label="Copy response"
          >
            {copied ? (
              <Check className="w-3.5 h-3.5 text-terminal" />
            ) : (
              <Copy className="w-3.5 h-3.5" />
            )}
          </button>
        )}
      </div>
    </div>
  );
}
