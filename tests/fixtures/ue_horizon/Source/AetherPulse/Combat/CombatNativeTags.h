// Copyright AetherPulse. All Rights Reserved.
#pragma once

#include "NativeGameplayTags.h"
#include "CombatNativeTags.generated.h"

// Extern declarations bridge to the definitions in the .cpp - these declare the
// C++ constant, not the tag string, so the literal index must NOT treat them as
// tag literals (only the DEFINE side registers the string).
UE_DECLARE_GAMEPLAY_TAG_EXTERN(TAG_Ability_Burn);
UE_DECLARE_GAMEPLAY_TAG_EXTERN(TAG_State_Actor_Corroded);

// Dynamic (Blueprint-exposed) delegates: cross-boundary event join points.
DECLARE_DYNAMIC_MULTICAST_DELEGATE_OneParam(FOnBurnApplied, float, Damage);
DECLARE_DYNAMIC_DELEGATE_RetVal_OneParam(bool, FOnBurnQuery, int32, ZoneId);
