#!/usr/bin/env python
#
# Copyright (C) 2016 GNS3 Technologies Inc.
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

import os
import json
import asyncio
import aiohttp
import shutil
import zipstream
import zipfile

from uuid import UUID, uuid4

from .node import Node
from .drawing import Drawing
from .topology import project_to_topology, load_topology
from .udp_link import UDPLink
from ..config import Config
from ..utils.path import check_path_allowed, get_default_project_directory


import logging
log = logging.getLogger(__name__)


class Project:
    """
    A project inside a controller

    :param project_id: force project identifier (None by default auto generate an UUID)
    :param path: path of the project. (None use the standard directory)
    :param status: Status of the project (opened / closed)
    """

    def __init__(self, name=None, project_id=None, path=None, controller=None, status="opened", filename=None, auto_start=False):

        self._controller = controller
        assert name is not None
        self._name = name
        self._auto_start = False
        self._status = status

        # Disallow overwrite of existing project
        if project_id is None and path is not None:
            if os.path.exists(path):
                raise aiohttp.web.HTTPForbidden(text="The path {} already exist.".format(path))

        if project_id is None:
            self._id = str(uuid4())
        else:
            try:
                UUID(project_id, version=4)
            except ValueError:
                raise aiohttp.web.HTTPBadRequest(text="{} is not a valid UUID".format(project_id))
            self._id = project_id

        if path is None:
            path = os.path.join(get_default_project_directory(), self._id)
        self.path = path

        if filename is not None:
            self._filename = filename
        else:
            self._filename = self.name + ".gns3"
        self.reset()

    def reset(self):
        """
        Called when open/close a project. Cleanup internal stuff
        """
        self._allocated_node_names = set()
        self._nodes = {}
        self._links = {}
        self._drawings = {}

        # Create the project on demand on the compute node
        self._project_created_on_compute = set()

    @property
    def auto_start(self):
        return self._auto_start

    @property
    def controller(self):
        return self._controller

    @property
    def name(self):
        return self._name

    @property
    def id(self):
        return self._id

    @property
    def path(self):
        return self._path

    @property
    def status(self):
        return self._status

    @path.setter
    def path(self, path):
        check_path_allowed(path)
        try:
            os.makedirs(path, exist_ok=True)
        except OSError as e:
            raise aiohttp.web.HTTPInternalServerError(text="Could not create project directory: {}".format(e))

        if '"' in path:
            raise aiohttp.web.HTTPForbidden(text="You are not allowed to use \" in the project directory path. Not supported by Dynamips.")

        self._path = path

    def _config(self):
        return Config.instance().get_section_config("Server")

    @property
    def captures_directory(self):
        """
        Location of the captures files
        """
        path = os.path.join(self._path, "project-files", "captures")
        os.makedirs(path, exist_ok=True)
        return path

    @property
    def pictures_directory(self):
        """
        Location of the images files
        """
        path = os.path.join(self._path, "project-files", "images")
        os.makedirs(path, exist_ok=True)
        return path

    @property
    def computes(self):
        """
        :return: Dictonnary of computes used by the project
        """
        return self._computes

    def remove_allocated_node_name(self, name):
        """
        Removes an allocated node name

        :param name: allocated node name
        """

        if name in self._allocated_node_names:
            self._allocated_node_names.remove(name)

    def update_allocated_node_name(self, base_name):
        """
        Updates a node name or generate a new if no node
        name is available.

        :param base_name: new node base name
        """

        if base_name is None:
            return None
        self.remove_allocated_node_name(base_name)
        if '{0}' in base_name or '{id}' in base_name:
            # base name is a template, replace {0} or {id} by an unique identifier
            for number in range(1, 1000000):
                name = base_name.format(number, id=number)
                if name not in self._allocated_node_names:
                    self._allocated_node_names.add(name)
                    return name
        else:
            if base_name not in self._allocated_node_names:
                self._allocated_node_names.add(base_name)
                return base_name
            # base name is not unique, let's find a unique name by appending a number
            for number in range(1, 1000000):
                name = base_name + str(number)
                if name not in self._allocated_node_names:
                    self._allocated_node_names.add(name)
                    return name
        raise aiohttp.web.HTTPConflict(text="A node name could not be allocated (node limit reached?)")

    def has_allocated_node_name(self, name):
        """
        Returns either a node name is already allocated or not.

        :param name: node name

        :returns: boolean
        """

        if name in self._allocated_node_names:
            return True
        return False

    def update_node_name(self, node, new_name):

        if new_name and node.name != new_name:
            return self.update_allocated_node_name(new_name)
        return new_name

    @asyncio.coroutine
    def add_node(self, compute, name, node_id, **kwargs):
        """
        Create a node or return an existing node

        :param kwargs: See the documentation of node
        """
        if node_id not in self._nodes:
            node = Node(self, compute, name, node_id=node_id, **kwargs)
            if compute not in self._project_created_on_compute:
                # For a local server we send the project path
                if compute.id == "local":
                    yield from compute.post("/projects", data={
                        "name": self._name,
                        "project_id": self._id,
                        "path": self._path
                    })
                else:
                    yield from compute.post("/projects", data={
                        "name": self._name,
                        "project_id": self._id,
                    })

                self._project_created_on_compute.add(compute)
            yield from node.create()
            self._nodes[node.id] = node
            self.controller.notification.emit("node.created", node.__json__())
            self.dump()
            return node
        return self._nodes[node_id]

    @asyncio.coroutine
    def delete_node(self, node_id):

        node = self.get_node(node_id)

        for link in list(self._links.values()):
            if node in link.nodes:
                yield from self.delete_link(link.id)

        self.remove_allocated_node_name(node.name)
        del self._nodes[node.id]
        yield from node.destroy()
        self.dump()
        self.controller.notification.emit("node.deleted", node.__json__())

    def get_node(self, node_id):
        """
        Return the node or raise a 404 if the node is unknown
        """
        try:
            return self._nodes[node_id]
        except KeyError:
            raise aiohttp.web.HTTPNotFound(text="Node ID {} doesn't exist".format(node_id))

    @property
    def nodes(self):
        """
        :returns: Dictionary of the nodes
        """
        return self._nodes

    @property
    def drawings(self):
        """
        :returns: Dictionary of the drawings
        """
        return self._drawings

    @asyncio.coroutine
    def add_drawing(self, drawing_id=None, **kwargs):
        """
        Create an drawing or return an existing drawing

        :param kwargs: See the documentation of drawing
        """
        if drawing_id not in self._drawings:
            drawing = Drawing(self, drawing_id=drawing_id, **kwargs)
            self._drawings[drawing.id] = drawing
            self.controller.notification.emit("drawing.created", drawing.__json__())
            self.dump()
            return drawing
        return self._drawings[drawing_id]

    def get_drawing(self, drawing_id):
        """
        Return the Drawing or raise a 404 if the drawing is unknown
        """
        try:
            return self._drawings[drawing_id]
        except KeyError:
            raise aiohttp.web.HTTPNotFound(text="Drawing ID {} doesn't exist".format(drawing_id))

    @asyncio.coroutine
    def delete_drawing(self, drawing_id):
        drawing = self.get_drawing(drawing_id)
        del self._drawings[drawing.id]
        self.dump()
        self.controller.notification.emit("drawing.deleted", drawing.__json__())

    @asyncio.coroutine
    def add_link(self, link_id=None):
        """
        Create a link. By default the link is empty
        """
        if link_id and link_id in self._links:
            return self._links[link.id]
        link = UDPLink(self, link_id=link_id)
        self._links[link.id] = link
        self.dump()
        return link

    @asyncio.coroutine
    def delete_link(self, link_id):
        link = self.get_link(link_id)
        del self._links[link.id]
        yield from link.delete()
        self.dump()
        self.controller.notification.emit("link.deleted", link.__json__())

    def get_link(self, link_id):
        """
        Return the Link or raise a 404 if the link is unknown
        """
        try:
            return self._links[link_id]
        except KeyError:
            raise aiohttp.web.HTTPNotFound(text="Link ID {} doesn't exist".format(link_id))

    @property
    def links(self):
        """
        :returns: Dictionary of the Links
        """
        return self._links

    @asyncio.coroutine
    def close(self):
        for compute in self._project_created_on_compute:
            yield from compute.post("/projects/{}/close".format(self._id))
        self._cleanPictures()
        self.reset()
        self._status = "closed"

    def _cleanPictures(self):
        """
        Delete unused images
        """

        try:
            pictures = set(os.listdir(self.pictures_directory))
            for drawing in self._drawings.values():
                pictures.remove(drawing.ressource_filename)

            for pict in pictures:
                os.remove(os.path.join(self.pictures_directory, pict))
        except OSError as e:
            log.warning(str(e))

    @asyncio.coroutine
    def delete(self):
        yield from self.close()
        for compute in self._project_created_on_compute:
            yield from compute.delete("/projects/{}".format(self._id))
        shutil.rmtree(self.path, ignore_errors=True)

    @classmethod
    def _get_default_project_directory(cls):
        """
        Return the default location for the project directory
        depending of the operating system
        """

        server_config = Config.instance().get_section_config("Server")
        path = os.path.expanduser(server_config.get("projects_path", "~/GNS3/projects"))
        path = os.path.normpath(path)
        try:
            os.makedirs(path, exist_ok=True)
        except OSError as e:
            raise aiohttp.web.HTTPInternalServerError(text="Could not create project directory: {}".format(e))
        return path

    def _topology_file(self):
        return os.path.join(self.path, self._filename)

    @asyncio.coroutine
    def open(self):
        """
        Load topology elements
        """
        if self._status == "opened":
            return

        self.reset()
        path = self._topology_file()
        if os.path.exists(path):
            topology = load_topology(path)["topology"]
            for compute in topology.get("computes", []):
                yield from self.controller.add_compute(**compute)
            for node in topology.get("nodes", []):
                compute = self.controller.get_compute(node.pop("compute_id"))
                name = node.pop("name")
                node_id = node.pop("node_id")
                yield from self.add_node(compute, name, node_id, **node)
            for link_data in topology.get("links", []):
                link = yield from self.add_link(link_id=link_data["link_id"])
                for node_link in link_data["nodes"]:
                    node = self.get_node(node_link["node_id"])
                    yield from link.add_node(node, node_link["adapter_number"], node_link["port_number"], label=node_link.get("label"))

            for drawing_data in topology.get("drawings", []):
                drawing = yield from self.add_drawing(**drawing_data)
        self._status = "opened"

    def export(self, include_images=False):
        """
        Export the project as zip. It's a ZipStream object.
        The file will be read chunk by chunk when you iterate on
        the zip.

        It will ignore some files like snapshots and

        :returns: ZipStream object
        """

        # To avoid issue with data not saved we disallow the export of a running topologie
        if self.is_running():
            raise aiohttp.web.HTTPConflict(text="Running topology could not be exported")

        z = zipstream.ZipFile()

        # First we process the .gns3 in order to be sure we don't have an error
        for file in os.listdir(self._path):
            if file.endswith(".gns3"):
                self._export_project_file(os.path.join(self._path, file), z, include_images)

        for root, dirs, files in os.walk(self._path, topdown=True):
            # Remove snapshots and capture
            if os.path.split(root)[-1:][0] == "project-files":
                dirs[:] = [d for d in dirs if d not in ("snapshots", "tmp")]

            # Ignore log files and OS noise
            files = [f for f in files if not f.endswith('_log.txt') and not f.endswith('.log') and f != '.DS_Store']

            for file in files:
                path = os.path.join(root, file)
                # Try open the file
                try:
                    open(path).close()
                except OSError as e:
                    msg = "Could not export file {}: {}".format(path, e)
                    log.warn(msg)
                    self.emit("log.warning", {"message": msg})
                    continue
            if file.endswith(".gns3"):
                pass
            else:
                z.write(path, os.path.relpath(path, self._path), compress_type=zipfile.ZIP_DEFLATED)
        return z

    def _export_project_file(self, path, z, include_images):
        """
        Take a project file (.gns3) and patch it for the export

        We rename the .gns3 project.gns3 to avoid the task to the client to guess the file name

        :param path: Path of the .gns3
        """

        # Image file that we need to include in the exported archive
        images = set()

        with open(path) as f:
            topology = json.load(f)
        if "topology" in topology and "nodes" in topology["topology"]:
            for node in topology["topology"]["nodes"]:
                if node["node_type"] in ["virtualbox", "vmware", "cloud"]:
                    raise aiohttp.web.HTTPConflict(text="Topology with a {} could not be exported".format(node["node_type"]))

                if "properties" in node and node["node_type"] != "Docker":
                    for prop, value in node["properties"].items():
                        if prop.endswith("image"):
                            node["properties"][prop] = os.path.basename(value)
                            if include_images is True:
                                images.add(value)

        for image in images:
            self._export_images(image, z)
        z.writestr("project.gns3", json.dumps(topology).encode())

    def _export_images(self, image, z):
        """
        Take a project file (.gns3) and export images to the zip

        :param image: Image path
        :param z: Zipfile instance for the export
        """
        from ..compute import MODULES

        for module in MODULES:
            try:
                img_directory = module.instance().get_images_directory()
            except NotImplementedError:
                # Some modules don't have images
                continue

            directory = os.path.split(img_directory)[-1:][0]

            if os.path.exists(image):
                path = image
            else:
                path = os.path.join(img_directory, image)

            if os.path.exists(path):
                arcname = os.path.join("images", directory, os.path.basename(image))
                z.write(path, arcname)
                break

    def is_running(self):
        """
        If a node is started or paused return True
        """
        for node in self._nodes.values():
            if node.status != "stopped":
                return True
        return False

    def dump(self):
        """
        Dump topology to disk
        """
        try:
            topo = project_to_topology(self)
            path = self._topology_file()
            log.debug("Write %s", path)
            with open(path + ".tmp", "w+") as f:
                json.dump(topo, f, indent=4, sort_keys=True)
            shutil.move(path + ".tmp", path)
        except OSError as e:
            raise aiohttp.web.HTTPInternalServerError(text="Could not write topology: {}".format(e))

    def __json__(self):

        return {
            "name": self._name,
            "project_id": self._id,
            "path": self._path,
            "filename": self._filename,
            "status": self._status
        }

    def __repr__(self):
        return "<gns3server.controller.Project {} {}>".format(self._name, self._id)