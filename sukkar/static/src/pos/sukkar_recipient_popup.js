import { Component, useState, onWillStart } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { useService } from "@web/core/utils/hooks";
import { usePos } from "@point_of_sale/app/store/pos_hook";
import { sukkarApplyFee } from "@sukkar/pos/sukkar_fee";
import { _t } from "@web/core/l10n/translation";

/**
 * Who receives this delivery, and where (owner's design, 29 Sep 2026).
 *
 * Pressing Delivery asks one question first — is the customer keeping it, or
 * sending it as a gift — because everything after that depends on the answer.
 *
 *   who        the customer, or one of the people they have sent to before,
 *              or someone new;
 *   address    that person's saved addresses, or a new one;
 *   recipient  a new person: name, phone, e-mail;
 *   address+   a new address: governorate → area picked, the rest typed.
 *
 * Whatever the counter types here is **saved under the customer**, so the next
 * order for the same mother, friend or office is two taps and no typing. The
 * address is chosen the same way as a customer's own address (LOCKED #3, #4,
 * #7): the area list is whatever the governorate contains, and block, street
 * and building are typed because Kuwait publishes no list good enough to pick
 * from.
 *
 * The chosen address is also written to the order's flat address fields, so the
 * kitchen board, dispatch, the driver receipt and the invoice read it exactly
 * as they did before.
 *
 * Choosing the address also **prices the delivery**: every area carries what
 * the shop charges to drive there (the team's own price list, 29 Sep 2026), so
 * the fee lands on the order by itself. The cashier can still override it from
 * the Delivery Fee button.
 */
export class SukkarRecipientPopup extends Component {
    static template = "sukkar.RecipientPopup";
    static components = { Dialog };
    static props = { order: Object, close: Function };

    setup() {
        this.orm = useService("orm");
        this.pos = usePos();
        const saved = this.props.order.sukkarDelivery || {};
        this.state = useState({
            // "who" · "address" · "new-person" · "new-address"
            step: "who",
            loading: true,
            saving: false,
            error: "",
            customer: false,
            recipients: [],
            // the person being delivered to, once picked
            personId: saved.sukkar_recipient_id || 0,
            isGift: Boolean(saved.sukkar_recipient_id),
            addressId: saved.sukkar_address_id || 0,
            // The driver belongs to THIS order only (owner, 2 Oct 2026).
            driver_id: saved.sukkar_driver_id || 0,
            drivers: [],
            // what the driver should know about this delivery (owner, 2 Oct 2026)
            deliveryNote: saved.sukkar_delivery_note || "",
            showNote: Boolean(saved.sukkar_delivery_note),
            states: [],
            areas: [],
            person: { name: "", phone: "", email: "" },
            address: this._blankAddress(),
        });
        onWillStart(async () => {
            const customer = this.props.order.get_partner();
            const [people, drivers, states] = await Promise.all([
                customer
                    ? this.orm.call("res.partner", "sukkar_pos_people", [customer.id])
                    : { customer: false, recipients: [] },
                this.orm.searchRead("res.users",
                    [["sukkar_is_driver", "=", true]], ["name"]),
                this.orm.searchRead("res.country.state",
                    [["country_id.code", "=", "KW"]], ["name"]),
            ]);
            this.state.customer = people.customer;
            this.state.recipients = people.recipients;
            this.state.drivers = drivers;
            this.state.states = states;
            this.state.loading = false;
            if (!customer) {
                this.state.error = _t(
                    "Pick the customer first — a gift is saved under the person paying.");
            }
        });
    }

    _blankAddress() {
        return {
            label: "", state_id: 0, area_id: 0, block: "", street: "",
            avenue: "", building: "", floor: "", apartment: "", directions: "",
        };
    }

    // --------------------------------------------------------------- who
    get people() {
        return this.state.customer
            ? [this.state.customer, ...this.state.recipients]
            : [];
    }

    get person() {
        return this.people.find((p) => p.id === this.state.personId) || false;
    }

    /** The chosen person's addresses. Always a list, even before one is
        chosen, so the screen has nothing to trip over mid-render. */
    get addresses() {
        return this.person?.addresses || [];
    }

    /** What a recipient's row says under their name: their phone, and how many
        addresses the shop already has for them. */
    summarise(who) {
        const count = who?.addresses?.length || 0;
        return [
            who?.phone,
            count ? `${count} ${count === 1 ? _t("address") : _t("addresses")}` : "",
        ].filter(Boolean).join(" · ");
    }

    /** The customer keeps it: their own addresses, no recipient on the order. */
    chooseCustomer() {
        if (!this.state.customer) {
            return;
        }
        this.state.isGift = false;
        this.state.personId = this.state.customer.id;
        this.state.error = "";
        this.state.step = "address";
    }

    /** A gift: the people this customer has sent to before, plus a new one. */
    chooseGift() {
        if (!this.state.customer) {
            return;
        }
        this.state.isGift = true;
        this.state.personId = 0;
        this.state.error = "";
        this.state.step = "gift";
    }

    pickRecipient(recipientId) {
        this.state.personId = recipientId;
        this.state.addressId = 0;
        this.state.error = "";
        this.state.step = "address";
    }

    // ------------------------------------------------------- a new recipient
    startNewPerson() {
        this.state.person = { name: "", phone: "", email: "" };
        this.state.address = this._blankAddress();
        this.state.areas = [];
        this.state.error = "";
        this.state.step = "new-person";
    }

    /** A recipient is saved with their first address in one go — a name with
        nowhere to deliver to is of no use to the driver. */
    async saveNewPerson() {
        const p = this.state.person;
        if (!p.name.trim() || !p.phone.trim()) {
            this.state.error = _t("A name and a phone number are needed.");
            return;
        }
        if (!Number(this.state.address.area_id)) {
            this.state.error = _t("Choose the governorate and the area.");
            return;
        }
        this.state.saving = true;
        try {
            const people = await this.orm.call(
                "res.partner", "sukkar_pos_create_recipient",
                [this.state.customer.id, {
                    name: p.name.trim(),
                    phone: p.phone.trim(),
                    email: p.email.trim(),
                    address: this._addressPayload(),
                }]);
            this.state.customer = people.customer;
            this.state.recipients = people.recipients;
            // Straight onto the person just created, with their new address.
            const added = this.state.recipients.find(
                (r) => r.name === p.name.trim() && r.phone === p.phone.trim());
            this.state.personId = added ? added.id : 0;
            this.state.addressId = added?.addresses?.[0]?.id || 0;
            this.state.error = "";
            this.state.step = "address";
        } catch (error) {
            this.state.error = this._message(error, _t("The recipient could not be saved."));
        } finally {
            this.state.saving = false;
        }
    }

    // --------------------------------------------------------- a new address
    startNewAddress() {
        this.state.address = this._blankAddress();
        this.state.areas = [];
        this.state.error = "";
        this.state.step = "new-address";
    }

    /** The areas are whatever this governorate contains — never a free list. */
    async onGovernorateChange() {
        const stateId = Number(this.state.address.state_id) || 0;
        this.state.address.area_id = 0;
        this.state.areas = stateId
            ? await this.orm.searchRead(
                  "sukkar.area", [["state_id", "=", stateId]], ["display_name"],
                  { order: "name" })
            : [];
    }

    _addressPayload() {
        const a = this.state.address;
        return {
            ...a,
            state_id: Number(a.state_id) || false,
            area_id: Number(a.area_id) || false,
        };
    }

    async saveNewAddress() {
        if (!Number(this.state.address.area_id)) {
            this.state.error = _t("Choose the governorate and the area.");
            return;
        }
        this.state.saving = true;
        try {
            const card = await this.orm.call(
                "res.partner", "sukkar_pos_add_address",
                [this.state.personId, this._addressPayload()]);
            this.person.addresses.push(card);
            this.state.addressId = card.id;
            this.state.error = "";
            this.state.step = "address";
        } catch (error) {
            this.state.error = this._message(error, _t("The address could not be saved."));
        } finally {
            this.state.saving = false;
        }
    }

    // ------------------------------------------------------------- finishing
    /** Picking the address prices the delivery straight away, so the amount is
        on the ticket before the cashier leaves this screen — waiting until Save
        made it look as though nothing had happened. */
    async pickAddress(addressId) {
        this.state.addressId = addressId;
        const address = this.chosenAddress;
        if (address) {
            await sukkarApplyFee(this.pos, this.props.order, address.fee || 0);
        }
    }

    /** What an address costs to drive to, for its button. An area the shop
        does not deliver to shows nothing rather than a misleading zero. */
    feeLabel(address) {
        return address?.fee
            ? this.env.utils.formatCurrency(address.fee)
            : _t("no fee set");
    }

    get chosenAddress() {
        return this.person?.addresses?.find((a) => a.id === this.state.addressId)
            || false;
    }

    back() {
        this.state.error = "";
        const s = this.state;
        s.step = s.step === "address"
            ? (s.isGift ? "gift" : "who")
            : s.step === "new-address" ? "address"
            : s.step === "new-person" ? "gift"
            : "who";
    }

    async confirm() {
        const person = this.person;
        const address = this.chosenAddress;
        if (!person) {
            this.state.error = _t("Choose who receives this order.");
            return;
        }
        if (!address) {
            this.state.error = _t("Choose an address, or add one.");
            return;
        }
        // The area prices the delivery. The cashier can still change it from
        // the Delivery Fee button afterwards.
        await sukkarApplyFee(this.pos, this.props.order, address.fee || 0);
        this.props.order.sukkarSetDelivery({
            sukkar_recipient_id: this.state.isGift ? person.id : false,
            sukkar_address_id: address.id,
            sukkar_receiver_name: person.name,
            sukkar_receiver_phone: person.phone,
            sukkar_addr_governorate_id: address.governorate_id || false,
            sukkar_addr_area: address.area,
            sukkar_addr_block: address.block,
            sukkar_addr_street: address.street,
            sukkar_addr_avenue: address.avenue,
            sukkar_addr_building: address.building,
            sukkar_addr_floor: address.floor,
            sukkar_addr_apartment: address.apartment,
            sukkar_addr_directions: address.directions,
            sukkar_delivery_note: this.state.deliveryNote.trim(),
            sukkar_driver_id: Number(this.state.driver_id) || false,
        });
        this.props.close();
    }

    /** Wipes who and where only — the day, the window and the type of the
        order belong to the screen that opened this one. */
    clear() {
        this.props.order.sukkarSetDelivery({
            sukkar_recipient_id: false,
            sukkar_address_id: false,
            sukkar_receiver_name: "",
            sukkar_receiver_phone: "",
            sukkar_addr_governorate_id: false,
            sukkar_addr_area: "",
            sukkar_addr_block: "",
            sukkar_addr_street: "",
            sukkar_addr_avenue: "",
            sukkar_addr_building: "",
            sukkar_addr_floor: "",
            sukkar_addr_apartment: "",
            sukkar_addr_directions: "",
            sukkar_delivery_note: "",
            sukkar_driver_id: false,
        });
        this.props.close();
    }

    _message(error, fallback) {
        return error?.data?.message || error?.message || fallback;
    }
}
