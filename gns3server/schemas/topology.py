#!/usr/bin/env python
#
# Copyright (C) 2015 GNS3 Technologies Inc.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

#
# This file contains the validation for checking a .gns3 file
#

from .compute import COMPUTE_OBJECT_SCHEMA
from .drawing import DRAWING_OBJECT_SCHEMA
from .link import LINK_OBJECT_SCHEMA
from .node import NODE_OBJECT_SCHEMA


TOPOLOGY_SCHEMA = {
    "$schema": "http://json-schema.org/draft-04/schema#",
    "description": "The topology",
    "type": "object",
    "properties": {
        "project_id": {
            "description": "Project UUID",
            "type": "string",
            "minLength": 36,
            "maxLength": 36,
            "pattern": "^[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}$"
        },
        "type": {
            "description": "Type of file. It's always topology",
            "enum": ["topology"]
        },
        "auto_start": {
            "description": "Start the topology when opened",
            "type": "boolean"
        },
        "revision": {
            "description": "Version of the .gns3 specification.",
            "type": "integer"
        },
        "version": {
            "description": "Version of the GNS3 software which have update the file for the last time",
            "type": "string"
        },
        "name": {
            "type": "string",
            "description": "Name of the project"
        },
        "topology": {
            "description": "The topology content",
            "type": "object",
            "properties": {
                "computes": {
                    "description": "Computes servers",
                    "type": "array",
                    "items": COMPUTE_OBJECT_SCHEMA
                },
                "drawings": {
                    "description": "Drawings elements",
                    "type": "array",
                    "items": DRAWING_OBJECT_SCHEMA
                },
                "links": {
                    "description": "Link elements",
                    "type": "array",
                    "items": LINK_OBJECT_SCHEMA
                },
                "nodes": {
                    "description": "Nodes elements",
                    "type": "array",
                    "items": NODE_OBJECT_SCHEMA
                }
            },
            "required": ["nodes", "links", "drawings", "computes"],
            "additionalProperties": False
        }
    },
    "required": [
        "project_id", "type", "revision", "version", "name", "topology"
    ],
    "additionalProperties": False
}