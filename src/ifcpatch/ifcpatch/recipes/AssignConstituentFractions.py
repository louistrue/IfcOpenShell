# IfcPatch - IFC patching utility
# Copyright (C) 2024 Louis Trümpler <louis@lt.plus>
#
# This file is part of IfcPatch.
#
# IfcPatch is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# IfcPatch is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with IfcPatch.  If not, see <http://www.gnu.org/licenses/>.

import ifcopenshell
import ifcopenshell.util.element
from ifcopenshell.util.unit import calculate_unit_scale
from logging import Logger
from collections import defaultdict
from typing import List, Dict, Tuple, Optional

class Patcher:
    """
    Assigns fractions to material constituents based on their "width".
    
    In Reference View MVD, material layer information is exchanged through 
    IfcMaterialConstituentSet and IfcPhysicalComplexQuantity instead of IfcMaterialLayerSet. 
    The layer thicknesses are stored as IfcQuantityLength within IfcPhysicalComplexQuantity 
    with a Discrimination of 'layer'.
    
    While the Fraction attribute in IfcMaterialConstituent is optional, it's valuable for 
    downstream applications as it indicates the relative proportion of each constituent in 
    the total material composition in a more straightforward way. 
    However, authoring applications often export material constituents without setting this attribute.
    
    This patcher helps maintain proper material information in Reference View exports by:
    1. Finding width quantities from IfcPhysicalComplexQuantity with 'layer' discrimination
    2. Using these widths to calculate relative proportions
    3. Setting the optional but valuable Fraction attribute
    
    For example, if a wall has constituents with widths of 0.1m and 0.2m, their fractions 
    would be set to 0.333 and 0.667 respectively.
    
    References:
    - IFC4 Reference View MVD: https://standards.buildingsmart.org/MVD/RELEASE/IFC4/ADD2_TC1/RV1_2/HTML/
    - IfcMaterialConstituent: https://standards.buildingsmart.org/IFC/RELEASE/IFC4/ADD2_TC1/HTML/schema/ifcmaterialresource/lexical/ifcmaterialconstituent.htm
    """

    def __init__(self, src: str, file: ifcopenshell.file, logger: Logger, *args):
        self.src = src
        self.file = file
        self.logger = logger

    def patch(self):
        """Execute the patch to assign fractions to material constituents."""
        unit_scale = calculate_unit_scale(self.file)
        
        # Get length unit from file for display
        length_unit = "model units"
        for unit in self.file.by_type("IfcSIUnit"):
            if unit.UnitType == "LENGTHUNIT":
                prefix = getattr(unit, "Prefix", None)
                length_unit = f"{prefix if prefix else ''}{unit.Name}"
                break

        for constituent_set in self.file.by_type('IfcMaterialConstituentSet'):
            constituents = constituent_set.MaterialConstituents or []
            if not constituents:
                continue  # Skip if no constituents found

            # Find elements associated with this constituent set
            associated_elements = set(ifcopenshell.util.element.get_elements_by_material(self.file, constituent_set))
            if not associated_elements:
                continue

            # Calculate widths for constituents
            constituent_widths, total_width = self.calculate_constituent_widths(
                constituents, associated_elements, unit_scale
            )

            if total_width == 0.0:
                constituent_set_name = constituent_set.Name or "Unnamed Constituent Set"
                self.logger.warning(f"No widths found for constituents in set '{constituent_set_name}'. Skipping.")
                continue

            # Assign fractions based on widths
            for constituent, width in constituent_widths.items():
                fraction = width / total_width
                constituent.Fraction = fraction
                self.logger.info(
                    f"Constituent: {constituent.Name}, "
                    f"Width: {width:.4f} {length_unit}, "
                    f"Fraction: {fraction:.4f}"
                )

    def get_element_quantities(self, element: ifcopenshell.entity_instance) -> Dict[str, float]:
        """Get width quantities for an element."""
        quantities = {}
        
        # Get quantities from IfcPhysicalComplexQuantity
        for rel in getattr(element, 'IsDefinedBy', []):
            if not rel.is_a('IfcRelDefinesByProperties'):
                continue
            definition = rel.RelatingPropertyDefinition
            if not definition.is_a('IfcElementQuantity'):
                continue
            for quantity in definition.Quantities:
                if not quantity.is_a('IfcPhysicalComplexQuantity'):
                    continue
                if quantity.Discrimination.lower() != 'layer':
                    continue
                for sub_quantity in quantity.HasQuantities:
                    if not sub_quantity.is_a('IfcQuantityLength'):
                        continue
                    if sub_quantity.Name.lower() != 'width':
                        continue
                    quantities[quantity.Name.lower()] = float(sub_quantity.LengthValue)
                
        return quantities

    def calculate_constituent_widths(
        self,
        constituents: List[ifcopenshell.entity_instance],
        elements: set[ifcopenshell.entity_instance],
        unit_scale: float
    ) -> Tuple[Dict[ifcopenshell.entity_instance, float], float]:
        """Calculate the widths of constituents based on associated quantities."""
        constituents_by_name = defaultdict(list)
        for constituent in constituents:
            constituent_name = (constituent.Name or "Unnamed Constituent").strip().lower()
            constituents_by_name[constituent_name].append(constituent)

        # Collect all quantities from all elements
        element_quantities = {}
        for element in elements:
            element_quantities.update(self.get_element_quantities(element))

        constituent_widths = {}
        total_width = 0.0

        for constituent_name, constituents_list in constituents_by_name.items():
            width = element_quantities.get(constituent_name, 0.0)
            for constituent in constituents_list:
                constituent_widths[constituent] = width
                total_width += width

        return constituent_widths, total_width
