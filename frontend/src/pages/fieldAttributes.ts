import { useEffect } from 'react';
import type { RefObject } from 'react';

/**
 * The Input primitive's props are frozen and carry no `autoComplete`/`name`,
 * so set them on the DOM nodes — where the browser and password managers
 * read them anyway. Keyed by input id.
 */
export function useFieldAttributes(
  formRef: RefObject<HTMLFormElement>,
  fields: Record<string, Record<string, string>>,
): void {
  useEffect(() => {
    const form = formRef.current;
    if (!form) return;
    for (const [id, attributes] of Object.entries(fields)) {
      const field = form.querySelector<HTMLInputElement>(`#${id}`);
      if (!field) continue;
      for (const [name, value] of Object.entries(attributes)) {
        field.setAttribute(name, value);
      }
    }
    // The attribute map is static per form; run once on mount.
  }, []);
}
