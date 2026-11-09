import {action, element, field, statusMessage} from "./controls.mjs";

/** Native HA media selector with manual ID fallback and revocable previews. */
export function renderReference(root, values, update, hass) {
  root.replaceChildren(element("h2", "Style reference"));
  root.append(element("p", "Select an image in Home Assistant media or paste its media-source ID. Previews are authenticated and read-only.", "muted"));
  const source = field("Reference media-source ID", values.reference_media_content_id,
    (v) => select(v),
    {placeholder: "media-source://media_source/local/icons/reference.png"});
  root.append(source.wrapper);
  const picker = element("ha-selector");
  picker.selector = {media: {accept: ["image/*"]}};
  picker.hass = hass;
  picker.value = values.reference_media_content_id
    ? {media_content_id: values.reference_media_content_id, media_content_type: "image"} : undefined;
  picker.addEventListener("value-changed", (event) => {
    const selection = event.detail?.value;
    if (event.detail && "value" in event.detail) select(selection?.media_content_id || "");
  });
  root.append(picker);
  const status = element("p", "", "muted");
  status.setAttribute("role", "status");
  const preview = element("div", undefined, "reference-preview");
  let objectURL = null;
  let controller = null;
  let sequence = 0;
  function dispose() {
    sequence++;
    if (controller) controller.abort();
    controller = null;
    if (objectURL) URL.revokeObjectURL(objectURL);
    objectURL = null;
    preview.replaceChildren();
  }
  function select(id) {
    dispose();
    status.textContent = "";
    source.input.value = id;
    picker.value = id ? {media_content_id:id,media_content_type:"image"} : undefined;
    update("reference_media_content_id", id);
  }
  const load = action("Preview selected image", async () => {
    dispose();
    const id = source.input.value.trim();
    if (!id.startsWith("media-source://")) {
      status.textContent = "Enter a valid media-source ID.";
      return;
    }
    const version = sequence;
    controller = new AbortController();
    status.textContent = "Loading reference…";
    try {
      const url = "/api/activity_images/settings/reference?media_id=" + encodeURIComponent(id);
      const response = await hass.fetchWithAuth(url, {signal: controller.signal});
      if (!response.ok) throw new Error("Reference unavailable (" + response.status + ")");
      const data = await response.blob();
      if (!data.type.startsWith("image/")) throw new Error("Reference is not an image");
      if (version !== sequence) return;
      objectURL = URL.createObjectURL(data);
      const image = element("img");
      image.alt = "Selected style reference";
      image.addEventListener("error", () => {
        if (version !== sequence) return;
        dispose();
        status.textContent = "Preview failed: image could not be decoded.";
      });
      image.src = objectURL;
      preview.replaceChildren(image);
      status.textContent = "Reference preview loaded. Saving is still required.";
    } catch (error) {
      if (version === sequence) status.textContent = "Preview failed: " + statusMessage(error);
    }
  });
  root.append(load, status, preview);
  return dispose;
}
