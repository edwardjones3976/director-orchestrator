"use strict";
// Local-only document navigation. No network, storage, tracking or HTML injection.
const filter = document.getElementById("document-filter");
if (filter) {
  const items = Array.from(document.querySelectorAll("[data-document]"));
  const count = document.getElementById("document-count");
  filter.addEventListener("input", () => {
    const terms = filter.value.toLocaleLowerCase().trim().split(/\s+/).filter(Boolean);
    let visible = 0;
    for (const item of items) {
      item.hidden = !terms.every(term => item.textContent.toLocaleLowerCase().includes(term));
      if (!item.hidden) visible += 1;
    }
    count.textContent = `${visible} of ${items.length} documents shown`;
  });
}
