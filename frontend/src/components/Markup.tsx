import { useMemo } from 'react';
import { cleanAi, toHtml } from '../lib/markup';

/** Renders dataset or AI text (Markdown + figures), sanitised. `ai` also rewrites stray LaTeX. */
export function Markup({ text, inline = false, ai = false, className = '' }: {
  text: string; inline?: boolean; ai?: boolean; className?: string;
}) {
  const html = useMemo(() => toHtml(ai ? cleanAi(text) : text, inline), [text, inline, ai]);
  const Tag = inline ? 'span' : 'div';
  return <Tag className={`markup ${className}`} dangerouslySetInnerHTML={{ __html: html }} />;
}
