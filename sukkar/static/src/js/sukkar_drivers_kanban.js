import { onWillStart, useEffect, useState, useSubEnv } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";
import { patch } from "@web/core/utils/patch";
import { registry } from "@web/core/registry";
import { kanbanView } from "@web/views/kanban/kanban_view";
import { KanbanController } from "@web/views/kanban/kanban_controller";
import { KanbanHeader } from "@web/views/kanban/kanban_header";
import { KanbanRenderer } from "@web/views/kanban/kanban_renderer";

const BOTH_KEY = "sukkar_drivers_both";

function readBoth() {
    try {
        return window.sessionStorage.getItem(BOTH_KEY) === "1";
    } catch {
        return false;
    }
}

function writeBoth(value) {
    try {
        window.sessionStorage.setItem(BOTH_KEY, value ? "1" : "0");
    } catch {
        // A private window without storage simply forgets the choice.
    }
}

/**
 * The Drivers screen (owner, 10 Oct 2026): buttons beside the search box
 * switch the columns between drivers, delivery date & time, and Both — a
 * column per slot with a lane per driver inside it (owner, 11 Oct 2026).
 */
export class SukkarDriversKanbanController extends KanbanController {
    setup() {
        super.setup(...arguments);
        this.sukkarBoard = useState({ both: readBoth() });
        useSubEnv({ sukkarBoard: this.sukkarBoard });
        // Odoo folds the "no driver" column; here it is the column orders are
        // dragged OUT of, so it opens once by itself.
        useEffect(() => {
            const root = this.model.root;
            if (this.sukkarOpenedUnassigned || !root.isGrouped
                    || root.groupByField?.name !== "driver_id") {
                return;
            }
            const unassigned = root.groups.find((group) => !group.value);
            if (unassigned) {
                this.sukkarOpenedUnassigned = true;
                if (unassigned.isFolded) {
                    unassigned.toggle();
                }
            }
        });
    }

    get sukkarGroupedBy() {
        return (this.model.root.groupBy || [])[0] || "";
    }

    get sukkarMode() {
        if (this.sukkarGroupedBy === "delivery_when") {
            return this.sukkarBoard.both ? "both" : "when";
        }
        return "driver";
    }

    sukkarShow(mode) {
        this.sukkarBoard.both = mode === "both";
        writeBoth(this.sukkarBoard.both);
        this.sukkarGroupBy(mode === "driver" ? "driver_id" : "delivery_when");
    }

    sukkarGroupBy(fieldName) {
        const searchModel = this.env.searchModel;
        const groupBys = searchModel.getSearchItems((item) => item.type === "groupBy");
        const target = groupBys.find((item) => item.fieldName === fieldName);
        for (const item of groupBys) {
            if (item.isActive && item !== target) {
                searchModel.toggleSearchItem(item.id);
            }
        }
        if (target && !target.isActive) {
            searchModel.toggleSearchItem(target.id);
        }
    }
}

/**
 * Both: inside each slot column, a lane for Unassigned and for every driver,
 * side by side, empty or not. Dragging a card to another lane of the same
 * slot gives the order to that driver; moving it to another slot is done on
 * Dispatch, so a lane of a different slot does not accept the card.
 */
export class SukkarDriversKanbanRenderer extends KanbanRenderer {
    static template = "sukkar.DriversKanbanRenderer";

    setup() {
        super.setup(...arguments);
        this.sukkarOrm = useService("orm");
        this.sukkarDrag = useState({ groupId: null, recordId: null, resId: null, over: null });
        this.sukkarDrivers = [];
        onWillStart(async () => {
            this.sukkarDrivers = await this.sukkarOrm.searchRead(
                "res.users", [["sukkar_is_driver", "=", true]], ["name"], { order: "name" });
        });
    }

    get sukkarLanes() {
        return Boolean(this.env.sukkarBoard?.both)
            && this.props.list.groupByField?.name === "delivery_when";
    }

    sukkarDriverOf(record) {
        const value = record.data.driver_id;
        if (!value) {
            return { id: false, name: "" };
        }
        if (Array.isArray(value)) {
            return { id: value[0], name: value[1] };
        }
        return { id: value.id, name: value.display_name };
    }

    sukkarLanesFor(group) {
        const lanes = [{ key: "none", driverId: false, name: _t("Unassigned"), records: [] }];
        for (const driver of this.sukkarDrivers) {
            lanes.push({ key: `d${driver.id}`, driverId: driver.id, name: driver.name, records: [] });
        }
        for (const record of group.list.records) {
            const driver = this.sukkarDriverOf(record);
            let lane = lanes.find((l) => l.driverId === driver.id);
            if (!lane) {
                // Someone who is not flagged as a driver still gets a lane.
                lane = { key: `d${driver.id}`, driverId: driver.id, name: driver.name, records: [] };
                lanes.push(lane);
            }
            lane.records.push(record);
        }
        return lanes;
    }

    sukkarDragStart(ev, group, record) {
        this.sukkarDrag.groupId = group.id;
        this.sukkarDrag.recordId = record.id;
        this.sukkarDrag.resId = record.resId;
        ev.dataTransfer.effectAllowed = "move";
        ev.dataTransfer.setData("text/plain", String(record.resId));
    }

    sukkarDragOver(ev, group, lane) {
        if (this.sukkarDrag.groupId !== group.id) {
            return; // another slot: not a drop target
        }
        ev.preventDefault();
        ev.dataTransfer.dropEffect = "move";
        this.sukkarDrag.over = `${group.id}-${lane.key}`;
    }

    sukkarDragEnd() {
        Object.assign(this.sukkarDrag, { groupId: null, recordId: null, resId: null, over: null });
    }

    async sukkarDrop(ev, group, lane) {
        ev.preventDefault();
        const { groupId, resId } = this.sukkarDrag;
        this.sukkarDragEnd();
        if (groupId !== group.id || !resId) {
            return;
        }
        await this.sukkarOrm.write("sukkar.production", [resId], {
            driver_id: lane.driverId || false,
        });
        await this.props.list.model.load();
    }
}

registry.category("views").add("sukkar_drivers_kanban", {
    ...kanbanView,
    Controller: SukkarDriversKanbanController,
    Renderer: SukkarDriversKanbanRenderer,
    buttonTemplate: "sukkar.DriversKanban.Buttons",
});

/** An order nobody is driving yet sits under "Unassigned", not "None".
    A driver's column carries Print (owner, 10 Oct 2026): his orders in a
    window, all ticked; untick what should not print. */
patch(KanbanHeader.prototype, {
    setup() {
        super.setup(...arguments);
        this.sukkarAction = useService("action");
    },

    get sukkarCanPrintDriver() {
        return this.props.list.resModel === "sukkar.production"
            && this.props.list.groupByField?.name === "driver_id"
            && Boolean(this.props.group.value)
            && this.props.group.count > 0;
    },

    async sukkarPrintDriver() {
        const ids = await this.orm.search(
            "sukkar.production", this.props.group.groupDomain);
        const action = await this.orm.call(
            "sukkar.driver.receipt.wizard", "sukkar_open", [ids]);
        await this.sukkarAction.doAction(action);
    },

    _getEmptyGroupLabel(fieldName) {
        if (fieldName === "driver_id" && this.props.list.resModel === "sukkar.production") {
            return _t("Unassigned");
        }
        return super._getEmptyGroupLabel(...arguments);
    },
});
