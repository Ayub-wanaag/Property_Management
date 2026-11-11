/*
 * Photo uploads on slow phones.
 *
 * Any form with class "pm-photo-form" gets three things, with no change to the server:
 *   1. Every photo is made smaller in the browser first (longest side 1600 pixels, JPEG).
 *   2. A progress bar while the photos are sent.
 *   3. If the connection drops, an error message and the same Send button to try again.
 * Without JavaScript the form still works as a normal form.
 */
(function () {
    "use strict";

    var MAX_SIDE = 1600;
    var QUALITY = 0.8;

    function compress(file) {
        return new Promise(function (resolve) {
            if (!file.type || file.type.indexOf("image/") !== 0 || file.type === "image/gif") {
                resolve(file);
                return;
            }
            var reader = new FileReader();
            reader.onerror = function () { resolve(file); };
            reader.onload = function () {
                var img = new Image();
                img.onerror = function () { resolve(file); };
                img.onload = function () {
                    var scale = Math.min(1, MAX_SIDE / Math.max(img.width, img.height));
                    var canvas = document.createElement("canvas");
                    canvas.width = Math.round(img.width * scale);
                    canvas.height = Math.round(img.height * scale);
                    canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
                    canvas.toBlob(function (blob) {
                        if (!blob || blob.size >= file.size) { resolve(file); return; }
                        var name = file.name.replace(/\.[^.]+$/, "") + ".jpg";
                        resolve(new File([blob], name, { type: "image/jpeg" }));
                    }, "image/jpeg", QUALITY);
                };
                img.src = reader.result;
            };
            reader.readAsDataURL(file);
        });
    }

    function setup(form) {
        var bar = form.querySelector(".pm-progress");
        var message = form.querySelector(".pm-upload-message");
        var button = form.querySelector("button[type=submit]");

        function show(text, isError) {
            if (!message) { return; }
            message.textContent = text;
            message.className = "pm-upload-message mt-2 " + (isError ? "text-danger" : "text-muted");
        }

        form.addEventListener("submit", function (event) {
            if (!window.FormData || !window.XMLHttpRequest || !window.Promise) { return; }
            event.preventDefault();
            if (button) { button.disabled = true; }
            show("Making the photos smaller...", false);

            var inputs = Array.prototype.slice.call(form.querySelectorAll("input[type=file]"));
            var jobs = [];
            inputs.forEach(function (input) {
                Array.prototype.slice.call(input.files).forEach(function (file) {
                    jobs.push(compress(file).then(function (small) { return { name: input.name, file: small }; }));
                });
            });

            Promise.all(jobs).then(function (parts) {
                var data = new FormData();
                Array.prototype.slice.call(form.elements).forEach(function (el) {
                    if (!el.name || el.type === "file" || el.disabled) { return; }
                    if ((el.type === "checkbox" || el.type === "radio") && !el.checked) { return; }
                    data.append(el.name, el.value);
                });
                parts.forEach(function (part) { data.append(part.name, part.file, part.file.name); });

                var xhr = new XMLHttpRequest();
                xhr.open("POST", form.action);
                xhr.upload.onprogress = function (e) {
                    if (e.lengthComputable && bar) {
                        bar.classList.remove("d-none");
                        bar.value = Math.round((e.loaded / e.total) * 100);
                    }
                    show("Sending... " + (e.lengthComputable ? Math.round((e.loaded / e.total) * 100) + "%" : ""), false);
                };
                xhr.onerror = function () {
                    if (button) { button.disabled = false; }
                    show("The connection dropped. Nothing was lost: press the button again to retry.", true);
                };
                xhr.onload = function () {
                    if (button) { button.disabled = false; }
                    if (xhr.status >= 200 && xhr.status < 300) {
                        if (xhr.responseURL && xhr.responseURL !== form.action) {
                            window.location.href = xhr.responseURL;   // the server redirected: success
                        } else {
                            document.open();                          // the form again, with an error message
                            document.write(xhr.responseText);
                            document.close();
                        }
                    } else {
                        show("Something went wrong (" + xhr.status + "). Please press the button again.", true);
                    }
                };
                xhr.send(data);
            });
        });
    }

    document.addEventListener("DOMContentLoaded", function () {
        Array.prototype.slice.call(document.querySelectorAll("form.pm-photo-form")).forEach(setup);
    });
})();
