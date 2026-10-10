htmx.defineExtension("json-enc", {
  onEvent: function (name, event) {
    if (name === "htmx:configRequest") {
      event.detail.headers["Content-Type"] = "application/json";
    }
  },
  encodeParameters: function (xhr, parameters) {
    xhr.overrideMimeType("text/json");
    return JSON.stringify(parameters);
  }
});
