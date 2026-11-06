package cmd

import (
	"fmt"

	"sand/internal/uds"
	"sand/proto"
)

func On(event *proto.RunEvent) string {
	return fmt.Sprint(event.Kind, proto.Version, uds.Send(event))
}

func Kind(client uds.Client) string {
	return client.Kind
}
