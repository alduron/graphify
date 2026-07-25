// Copyright AetherPulse. All Rights Reserved.
#pragma once

#include "CoreMinimal.h"
#include "CombatNativeTags.h"

// The header BurnAbility.cpp includes. Without it in the fixture the include resolves onto the .cpp's
// OWN file node (a .h and its sibling .cpp collapse to one `burnability` id, and the #1475
// disambiguation only salts them apart when BOTH exist), emitting a self-loop that
// test_pipeline_no_self_loops correctly rejects.
class UBurnAbility
{
public:
    void Activate();
    void Fire();
};
