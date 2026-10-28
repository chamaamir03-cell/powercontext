export interface TextChunk {
  text: string;
  index: number;
}

const CHUNK_SIZE = 450;
const CHUNK_OVERLAP = 64;

export function chunkText(input: string): TextChunk[] {
  const text = input.replace(/\u0000/g, "").replace(/\r\n?/g, "\n").trim();
  if (!text) return [];

  const paragraphs = text.split(/\n{2,}/).map((part) => part.trim()).filter(Boolean);
  const chunks: string[] = [];
  let current = "";

  const pushCurrent = () => {
    if (current.trim()) chunks.push(current.trim());
    current = "";
  };

  for (const paragraph of paragraphs) {
    const units = Array.from(paragraph);
    if (units.length > CHUNK_SIZE) {
      pushCurrent();
      let offset = 0;
      while (offset < units.length) {
        const end = Math.min(offset + CHUNK_SIZE, units.length);
        const segment = units.slice(offset, end).join("").trim();
        if (segment) chunks.push(segment);
        if (end === units.length) break;
        offset = end - CHUNK_OVERLAP;
      }
      continue;
    }

    if (current && Array.from(`${current}\n\n${paragraph}`).length > CHUNK_SIZE) {
      pushCurrent();
    }
    current = current ? `${current}\n\n${paragraph}` : paragraph;
  }
  pushCurrent();

  return chunks.map((chunk, index) => ({ text: chunk, index }));
}
