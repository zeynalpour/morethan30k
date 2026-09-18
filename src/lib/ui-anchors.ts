// DOM anchor ids shared by more than one component (IDEAS N step 0).
//
// Kept in a neutral module so a panel that offers an "author it" affordance can
// point at the editor section that owns the control WITHOUT importing the
// editor component (ConfigEditor ⇄ ModulesPanel would otherwise be an import
// cycle).

/** The section in ConfigEditor that hosts the steps authoring controls. */
export const FLOW_STEPS_ANCHOR_ID = "flow-steps";
