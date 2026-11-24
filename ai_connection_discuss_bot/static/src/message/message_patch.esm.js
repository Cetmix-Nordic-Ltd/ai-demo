/* Copyright 2026 Cetmix Nordic Ltd
 * License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl). */

import {onMounted, useState} from "@odoo/owl";
import {Message} from "@mail/core/common/message";
import {_t} from "@web/core/l10n/translation";
import {patch} from "@web/core/utils/patch";
import {rpc} from "@web/core/network/rpc";
import {useService} from "@web/core/utils/hooks";

// Class of the block of confirmation links the bot adds under an answer
const ACTIONS_BLOCK_CLASS = "o_ai_bot_actions";

patch(Message.prototype, {
    setup() {
        super.setup(...arguments);
        this.aiBot = useState({actions: [], busy: false});
        this.aiBotNotification = useService("notification");
        onMounted(() => this.aiBotLoadActions());
    },

    /** Proposals of the message, when the bot's answer carries some. */
    async aiBotLoadActions() {
        const message = this.props.message;
        if (
            !message ||
            typeof message.id !== "number" ||
            !String(message.body || "").includes(ACTIONS_BLOCK_CLASS)
        ) {
            return;
        }
        try {
            this.aiBot.actions = await rpc("/ai_discuss_bot/message_actions", {
                message_id: message.id,
            });
        } catch {
            // The links in the message body still work
            this.aiBot.actions = [];
        }
    },

    async aiBotDecide(action, decision) {
        if (this.aiBot.busy) {
            return;
        }
        this.aiBot.busy = true;
        try {
            const updated = await rpc("/ai_discuss_bot/action/decide_inline", {
                action_id: action.id,
                decision,
            });
            const index = this.aiBot.actions.findIndex((a) => a.id === action.id);
            if (index >= 0) {
                this.aiBot.actions[index] = updated;
            }
            if (updated.state === "failed") {
                this.aiBotNotification.add(updated.error, {type: "danger"});
            }
        } catch {
            this.aiBotNotification.add(_t("The decision could not be saved."), {
                type: "danger",
            });
        } finally {
            this.aiBot.busy = false;
        }
    },
});
