/* Copyright 2026 Cetmix Nordic Ltd
 * License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl). */

import {registry} from "@web/core/registry";

registry.category("web_tour.tours").add("ai_discuss_bot_confirm_tour", {
    steps: () => [
        {
            content: "The proposal is shown with buttons, the links are hidden",
            trigger:
                ".o_ai_bot_proposal:contains('Call the customer') .o_ai_bot_confirm",
        },
        {
            trigger: ".o-mail-Message-body .o_ai_bot_actions:not(:visible)",
        },
        {
            content: "Confirm the proposal",
            trigger: ".o_ai_bot_proposal .o_ai_bot_confirm",
            run: "click",
        },
        {
            content: "The proposal is done",
            trigger: ".o_ai_bot_proposal .o_ai_bot_state:contains('Done')",
        },
    ],
});
