/** Textareas normalize CRLF. Preserve untouched source around the explicit edit. */
export function sourceEdit(original: string, display: string): string {
  const previous = original.replace(/\r\n/g, "\n");
  let start = 0,
    end = 0;
  while (
    start < previous.length &&
    start < display.length &&
    previous[start] === display[start]
  )
    start++;
  while (
    end < previous.length - start &&
    end < display.length - start &&
    previous[previous.length - 1 - end] === display[display.length - 1 - end]
  )
    end++;
  const offset = (index: number) => {
    let raw = 0,
      count = 0;
    while (count < index) {
      if (original[raw] === "\r" && original[raw + 1] === "\n") raw++;
      raw++;
      count++;
    }
    return raw;
  };
  const replacement = display.slice(start, display.length - end);
  return (
    original.slice(0, offset(start)) +
    (original.includes("\r\n")
      ? replacement.replace(/\n/g, "\r\n")
      : replacement) +
    original.slice(offset(previous.length - end))
  );
}
