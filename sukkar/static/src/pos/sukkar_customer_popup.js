import { Component, useState, onWillStart } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";

/**
 * Creating a customer at the till (owner's design, 22 Sep 2026).
 *
 * Odoo's own screen opens the back-office contact form — company or individual,
 * job position, tax id, three tabs of accounting. A cashier with a queue needs
 * two boxes and an address, so this is that screen and nothing else.
 *
 * Step 1  name, phone, e-mail.
 * Step 2  an address: what to call it, who receives at it, and where it is.
 *
 * Editing a customer opens this same form (owner, 2 Oct 2026), filled in, with
 * their saved addresses listed between the two steps: pick one to correct it,
 * or add another.
 *
 * The address is chosen, not typed, as far as the data allows: governorate,
 * then only the areas that governorate contains. Block, street and building
 * are typed, because Kuwait publishes no list of those complete enough to
 * choose from — and a list that is wrong is worse than a box.
 */
export class SukkarCustomerPopup extends Component {
    static template = "sukkar.CustomerPopup";
    static components = { Dialog };
    static props = {
        getPayload: Function,
        close: Function,
        partner: { type: Object, optional: true },
    };

    setup() {
        this.orm = useService("orm");
        this.pos = useService("pos");
        const partner = this.props.partner;
        this.state = useState({
            step: 1,
            saving: false,
            error: "",
            // set when editing an existing customer
            partnerId: partner?.id || 0,
            name: partner?.name || "",
            phone: partner?.phone || partner?.mobile || "",
            email: partner?.email || "",
            addresses: [],
            // the saved address being corrected; 0 for a new one
            addressId: 0,
            // the address, only when the cashier asks for one
            wantsAddress: false,
            states: [],
            areas: [],
            address: this._blankAddress(),
        });
        onWillStart(async () => {
            this.state.states = await this.orm.searchRead(
                "res.country.state", [["country_id.code", "=", "KW"]], ["name"]);
            if (this.isEdit) {
                await this._loadAddresses();
            }
        });
    }

    get isEdit() {
        return Boolean(this.state.partnerId);
    }

    get title() {
        if (this.state.step === 2) {
            return this.state.addressId ? _t("Edit Address") : _t("Address");
        }
        if (this.state.step === "list") {
            return _t("Addresses");
        }
        return this.isEdit ? _t("Edit Customer") : _t("New Customer");
    }

    async _loadAddresses() {
        const people = await this.orm.call(
            "res.partner", "sukkar_pos_people", [this.state.partnerId]);
        this.state.addresses = people.customer ? people.customer.addresses : [];
    }

    _blankAddress() {
        return {
            label: "", contact_name: "", contact_phone: "", contact_email: "",
            state_id: 0, area_id: 0, block: "", street: "", avenue: "",
            building: "", floor: "", apartment: "", directions: "",
        };
    }

    // ------------------------------------------------------------------ step 1
    get step1Valid() {
        return Boolean(this.state.name.trim() && this.state.phone.trim());
    }

    goToAddress() {
        if (!this.step1Valid) {
            this.state.error = _t("A name and a phone number are needed.");
            return;
        }
        // The address is for this customer, so it starts as this customer —
        // and stays editable, because whoever receives there may be someone else.
        const address = this._blankAddress();
        address.contact_name = this.state.name;
        address.contact_phone = this.state.phone;
        address.contact_email = this.state.email;
        this.state.address = address;
        this.state.addressId = 0;
        this.state.areas = [];
        this.state.error = "";
        this.state.wantsAddress = true;
        this.state.step = 2;
    }

    // ------------------------------------------------- editing: the addresses
    goToList() {
        // Only a way through to the addresses: the name and phone are checked
        // when the customer is saved, so an old customer with no phone on file
        // can still have an address corrected.
        this.state.error = "";
        this.state.step = "list";
    }

    async editAddress(card) {
        this.state.address = {
            ...this._blankAddress(),
            label: card.label,
            contact_name: card.contact_name,
            contact_phone: card.contact_phone,
            contact_email: card.contact_email,
            state_id: card.governorate_id || 0,
            block: card.block,
            street: card.street,
            avenue: card.avenue,
            building: card.building,
            floor: card.floor,
            apartment: card.apartment,
            directions: card.directions,
        };
        await this.onGovernorateChange();
        this.state.address.area_id = card.area_id || 0;
        this.state.addressId = card.id;
        this.state.error = "";
        this.state.step = 2;
    }

    // ------------------------------------------------------------------ step 2
    async onGovernorateChange() {
        const stateId = Number(this.state.address.state_id) || 0;
        this.state.address.area_id = 0;
        this.state.areas = stateId
            ? await this.orm.searchRead(
                  "sukkar.area", [["state_id", "=", stateId]], ["display_name"],
                  { order: "name" })
            : [];
    }

    back() {
        this.state.step = this.isEdit && this.state.step === 2 ? "list" : 1;
        this.state.error = "";
    }

    /** Editing: save the address on screen and return to the list. */
    async saveAddress() {
        const address = this.state.address;
        if (!Number(address.area_id)) {
            this.state.error = _t("Choose the governorate and the area.");
            return;
        }
        const vals = {
            ...address,
            state_id: Number(address.state_id) || false,
            area_id: Number(address.area_id) || false,
        };
        this.state.saving = true;
        try {
            if (this.state.addressId) {
                await this.orm.call("res.partner", "sukkar_pos_update_address",
                    [this.state.addressId, vals]);
            } else {
                await this.orm.call("res.partner", "sukkar_pos_add_address",
                    [this.state.partnerId, vals]);
            }
            await this._loadAddresses();
            this.state.error = "";
            this.state.step = "list";
        } catch (error) {
            this.state.error = error?.data?.message || error?.message
                || _t("The address could not be saved.");
        } finally {
            this.state.saving = false;
        }
    }

    /** Editing: save the name, phone and e-mail and close. */
    async saveEdit() {
        if (!this.step1Valid) {
            this.state.step = 1;
            this.state.error = _t("A name and a phone number are needed.");
            return;
        }
        this.state.saving = true;
        try {
            const partnerId = await this.orm.call(
                "res.partner", "sukkar_update_from_pos", [this.state.partnerId, {
                    name: this.state.name.trim(),
                    phone: this.state.phone.trim(),
                    email: this.state.email.trim(),
                }]);
            const [partner] = await this.pos.data.read("res.partner", [partnerId]);
            this.props.getPayload(partner);
            this.props.close();
        } catch (error) {
            this.state.error = error?.data?.message || error?.message
                || _t("The customer could not be saved.");
        } finally {
            this.state.saving = false;
        }
    }

    // ------------------------------------------------------------------- saving
    async save() {
        if (!this.step1Valid) {
            this.state.step = 1;
            this.state.error = _t("A name and a phone number are needed.");
            return;
        }
        const address = this.state.address;
        if (this.state.wantsAddress && !address.area_id) {
            this.state.error = _t("Choose the governorate and the area.");
            return;
        }
        this.state.saving = true;
        try {
            const partnerId = await this.orm.call(
                "res.partner", "sukkar_create_from_pos", [{
                    name: this.state.name.trim(),
                    phone: this.state.phone.trim(),
                    email: this.state.email.trim(),
                    address: this.state.wantsAddress ? {
                        ...address,
                        state_id: Number(address.state_id) || false,
                        area_id: Number(address.area_id) || false,
                    } : false,
                }]);
            // Pull the new customer into the register and hand it back.
            const [partner] = await this.pos.data.read("res.partner", [partnerId]);
            this.props.getPayload(partner);
            this.props.close();
        } catch (error) {
            this.state.error = error?.data?.message || error?.message
                || _t("The customer could not be saved.");
        } finally {
            this.state.saving = false;
        }
    }
}
