// Copyright AetherPulse. All Rights Reserved.
#include "CombatNativeTags.h"

// The three native gameplay-tag definition forms the literal index must catch.
UE_DEFINE_GAMEPLAY_TAG(TAG_Ability_Burn, "Ability.Burn");
UE_DEFINE_GAMEPLAY_TAG_COMMENT(TAG_State_Actor_Corroded, "State.Actor.Corroded", "Actor is corroded");
UE_DEFINE_GAMEPLAY_TAG_STATIC(TAG_HitZone_Thruster_01, "HitZone.Thruster.01");

// A dotted config key referenced in more than one file -> promotes to config_key.
static const TCHAR* CombatConfigKey = TEXT("Game.Config.MaxPlayers");
