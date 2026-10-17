import { useRef, useState } from "@odoo/owl";
import { TextInputPopup } from "@point_of_sale/app/utils/input_popups/text_input_popup";

/** The longest side a note picture keeps. A phone photo is 4000px and several
    megabytes; the kitchen only needs to see it, and the till sends it inside
    the order, so it is made small here before it leaves the till. */
const MAX_SIDE = 1600;

/**
 * The Internal Note box with a picture (owner, 10 Oct 2026).
 *
 * The same box as Odoo's note popup — quick-note buttons, the text, Apply —
 * plus "Add image", which opens the till PC's own file picker. The picture
 * is kept on the line the note is for and goes with it to the kitchen.
 *
 * Resolves with { note, image }: image is base64 JPEG without its data: prefix,
 * or "" when there is none.
 */
export class SukkarNotePopup extends TextInputPopup {
    static template = "sukkar.NotePopup";
    static props = {
        ...TextInputPopup.props,
        startingImage: { type: String, optional: true },
    };
    static defaultProps = {
        ...TextInputPopup.defaultProps,
        startingImage: "",
    };

    setup() {
        super.setup();
        this.image = useState({ data: this.props.startingImage, error: "" });
        this.fileRef = useRef("file");
    }

    get canApply() {
        return Boolean(this.state.inputValue.trim() || this.image.data)
            || this.image.data !== this.props.startingImage;
    }

    get imageSrc() {
        return `data:image/jpeg;base64,${this.image.data}`;
    }

    pickImage() {
        this.fileRef.el.value = "";
        this.fileRef.el.click();
    }

    async onFileChosen(ev) {
        const file = ev.target.files && ev.target.files[0];
        if (!file) {
            return;
        }
        this.image.error = "";
        try {
            this.image.data = await shrinkToJpeg(file);
        } catch {
            this.image.error = "This file is not a picture the till can read.";
        }
    }

    removeImage() {
        this.image.data = "";
    }

    confirm() {
        this.props.getPayload({ note: this.state.inputValue, image: this.image.data });
        this.props.close();
    }
}

async function shrinkToJpeg(file) {
    const url = URL.createObjectURL(file);
    try {
        const img = await new Promise((resolve, reject) => {
            const el = new Image();
            el.onload = () => resolve(el);
            el.onerror = reject;
            el.src = url;
        });
        const scale = Math.min(1, MAX_SIDE / Math.max(img.naturalWidth, img.naturalHeight));
        const canvas = document.createElement("canvas");
        canvas.width = Math.round(img.naturalWidth * scale);
        canvas.height = Math.round(img.naturalHeight * scale);
        const ctx = canvas.getContext("2d");
        // A transparent PNG would turn black as a JPEG.
        ctx.fillStyle = "#fff";
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
        return canvas.toDataURL("image/jpeg", 0.85).split(",")[1];
    } finally {
        URL.revokeObjectURL(url);
    }
}
